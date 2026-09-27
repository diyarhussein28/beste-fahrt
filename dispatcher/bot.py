"""Telegram Dispatcher — القسم 7. Sends offer alerts with inline
accept/decline buttons, enforces the driver chat_id whitelist (القسم 10),
and drives escalation. Accepting a button here never books anything on the
platform — it only records the driver's intent and tells the manager; the
driver still opens the platform link and books it themselves (القسم 7.2).
"""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from shared.config import get_config, get_secrets
from shared.db import get_driver, get_driver_by_chat_id, get_job, record_dispatch_response, record_driver_location, try_claim_job
from shared.events import ack, consume
from shared.logging_config import configure_logging
from shared.models import JobStatus, RankedDriver, WatchStatus

from matcher.service import rank_job
from returns.finder import check_job_against_watch, find_before_dispatch
from returns.watches import close_watch, get_watch, open_watch, reopen_watch
from dispatcher import escalation, manager_commands, templates

log = logging.getLogger("dispatcher.bot")


def _keyboard(buttons: templates.Buttons) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in buttons]
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or query is None or query.data is None:
        return

    driver = await get_driver_by_chat_id(chat_id)
    if driver is None or not driver.active:
        # القسم 10: قائمة بيضاء بـ chat_id — تجاهل أي رسالة من غير سائق مسجّل
        # أو من سائق أوقفه المدير عبر /remove_driver.
        log.warning("callback from unknown/inactive chat_id ignored", extra={"extra_fields": {"chat_id": chat_id}})
        await query.answer("غير مصرح")
        return

    action, _, rest = query.data.partition(":")
    parts = rest.split(":")
    job_fp = parts[0]

    if action == "acc" or action == "accboth":
        claimed = await try_claim_job(job_fp, JobStatus.DISPATCHED)
        if not claimed:
            await query.answer("تم أخذها من سائق آخر بالفعل")
            await query.edit_message_text(query.message.text + "\n\n🚫 تم أخذها من سائق آخر")
            return
        await record_dispatch_response(job_fp, driver.id, "accept")
        await query.answer("تم تسجيل قبولك")
        await query.edit_message_text(query.message.text + "\n\n✅ لقد قبلت هذا العرض — افتح الرابط واحجزه في التطبيق")
        secrets = get_secrets()
        if secrets.telegram_manager_chat_id:
            await context.bot.send_message(
                secrets.telegram_manager_chat_id,
                f"✅ {driver.name} قبل العرض {job_fp[:8]}…",
            )

        # القسم 8.2: قبول رحلة ذهاب يفتح تلقائياً طلب عودة مفتوح، إلا إذا كان
        # السائق قد أخذ الرحلتين معاً بالفعل عبر "آخذ الاثنتين".
        return_config = get_config().return_trip
        if action == "acc" and return_config.enabled:
            job = await get_job(job_fp)
            if job is not None:
                watch = await open_watch(driver, job, return_config)
                if watch is not None:
                    log.info("return watch opened after acceptance", extra={"extra_fields": {"fp": job_fp, "watch_id": watch.id}})

    elif action == "dec":
        await record_dispatch_response(job_fp, driver.id, "decline")
        await query.answer("تم تسجيل الرفض")
        await query.edit_message_text(query.message.text + "\n\n❌ تم الرفض")

    elif action == "skip_return":
        watch_id = int(parts[1])
        await query.answer("سنواصل البحث عن عودة أخرى")
        await query.edit_message_text(query.message.text + "\n\n⏭️ يواصل النظام البحث عن رحلة عودة أخرى")
        await reopen_watch(watch_id)

    elif action == "declare_train":
        watch_id = int(parts[1])
        await query.answer("تم التسجيل — عودة سعيدة")
        await query.edit_message_text(query.message.text + "\n\n🚆 سيعود السائق بالقطار")
        await close_watch(watch_id, WatchStatus.CLOSED)

    else:
        log.warning("unknown callback action", extra={"extra_fields": {"data": query.data}})
        await query.answer()


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Live Location عبر Telegram — القسم 6.1 (أعلى دقة). Only recorded for
    a whitelisted, consenting driver; anything else is ignored per القسم 10.
    """
    chat_id = update.effective_chat.id if update.effective_chat else None
    location = update.message.location if update.message else None
    if chat_id is None or location is None:
        return

    driver = await get_driver_by_chat_id(chat_id)
    if driver is None or not driver.active:
        return
    if not driver.location_consent:
        # القسم 11.3: التتبّع فقط بموافقة صريحة.
        await update.message.reply_text("لم تُفعّل مشاركة الموقع بعد — تواصل مع المدير لتفعيلها.")
        return

    await record_driver_location(driver.id, location.latitude, location.longitude, source="live_share")


async def send_offer_to_driver(candidate: RankedDriver, job, rank: int) -> None:
    cfg = get_config()
    privacy = cfg.privacy
    return_cfg = cfg.return_trip

    # القسم 8.2/8.11: قبل إرسال أي عرض ذهاب بعيد، ابحث عن رحلة عودة وأرفقها
    # بنفس التنبيه — فقط لأفضل سائق (rank 1) ولديه نقطة أساس (منزل) محفوظة.
    ret = None
    if (
        rank == 1
        and return_cfg.enabled
        and (job.route_km or 0) >= return_cfg.trigger_min_outbound_km
        and candidate.driver.home_lat is not None
        and candidate.driver.home_lon is not None
    ):
        ret = await find_before_dispatch(job, candidate.driver.home_lat, candidate.driver.home_lon, return_cfg)

    if ret is not None:
        text, buttons = templates.render_combined_alert(job, candidate.approach_km, ret, privacy)
    else:
        text, buttons = templates.render_offer_alert(job, candidate.approach_km, privacy)

    await _APP.bot.send_message(
        candidate.driver.telegram_chat_id,
        text,
        reply_markup=_keyboard(buttons),
        disable_web_page_preview=True,
    )


async def send_escalation_to_manager(job, attempts: int) -> None:
    secrets = get_secrets()
    if not secrets.telegram_manager_chat_id:
        return
    await _APP.bot.send_message(secrets.telegram_manager_chat_id, templates.render_manager_escalation(job, attempts))


async def consume_dispatch_ready() -> None:
    async for message_id, payload in consume("dispatch.ready", "dispatcher", "dispatcher-1"):
        try:
            job, ranked = await rank_job(payload["fp"])
            if job is None or not ranked:
                continue
            config = get_config()
            if payload.get("mode") == "broadcast":
                await escalation.dispatch_broadcast(
                    job, ranked, top_n=config.matching.top_n_for_road_distance, send_to_driver=send_offer_to_driver
                )
            else:
                asyncio.create_task(
                    escalation.dispatch_sequential(
                        job, ranked, config.dispatch, send_offer_to_driver, send_escalation_to_manager
                    )
                )
        except Exception:
            log.exception("failed to process dispatch.ready event", extra={"extra_fields": {"payload": payload}})
        finally:
            await ack("dispatch.ready", "dispatcher", message_id)


async def consume_manager_alerts() -> None:
    secrets = get_secrets()
    async for message_id, payload in consume("alert.manager", "dispatcher", "dispatcher-1"):
        try:
            if secrets.telegram_manager_chat_id and isinstance(payload, dict):
                await _APP.bot.send_message(
                    secrets.telegram_manager_chat_id, templates.render_manager_text_alert(payload.get("message", ""))
                )
        except Exception:
            log.exception("failed to forward manager alert")
        finally:
            await ack("alert.manager", "dispatcher", message_id)


async def consume_return_matched() -> None:
    """القسم 8.10 (التنبيه الثاني) — رحلة عودة عُثر عليها لطلب مفتوح."""
    async for message_id, payload in consume("return.matched", "dispatcher", "dispatcher-1"):
        try:
            watch = await get_watch(payload["watch_id"])
            job = await get_job(payload["fp"])
            if watch is None or job is None:
                continue
            driver = await get_driver(watch.driver_id)
            if driver is None or driver.telegram_chat_id is None:
                continue

            candidate = check_job_against_watch(job, watch, get_config().return_trip)
            if candidate is None:
                continue

            hours_after_eta = max(0.0, (job.pickup_date - watch.available_at).total_seconds() / 3600) if job.pickup_date else 0.0
            text, buttons = templates.render_return_alert(
                candidate, hours_after_eta, candidate.remaining_km, get_config().privacy, watch.id
            )
            await _APP.bot.send_message(
                driver.telegram_chat_id, text, reply_markup=_keyboard(buttons), disable_web_page_preview=True
            )
        except Exception:
            log.exception("failed to process return.matched event", extra={"extra_fields": {"payload": payload}})
        finally:
            await ack("return.matched", "dispatcher", message_id)


_APP: Application


async def run() -> None:
    global _APP
    configure_logging()
    secrets = get_secrets()
    if not secrets.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    _APP = Application.builder().token(secrets.telegram_bot_token).build()
    _APP.add_handler(CallbackQueryHandler(on_callback))
    _APP.add_handler(MessageHandler(filters.LOCATION, on_location))
    _APP.add_handler(CommandHandler("start", manager_commands.cmd_start))
    _APP.add_handler(CommandHandler("consent_on", manager_commands.cmd_consent_on))
    _APP.add_handler(CommandHandler("consent_off", manager_commands.cmd_consent_off))
    _APP.add_handler(CommandHandler("drivers", manager_commands.cmd_drivers))
    _APP.add_handler(CommandHandler("add_driver", manager_commands.cmd_add_driver))
    _APP.add_handler(CommandHandler("remove_driver", manager_commands.cmd_remove_driver))
    _APP.add_handler(CommandHandler("activate_driver", manager_commands.cmd_activate_driver))
    _APP.add_handler(CommandHandler("set_home", manager_commands.cmd_set_home))

    await _APP.initialize()
    await _APP.start()
    await _APP.updater.start_polling()
    log.info("dispatcher bot started")
    try:
        await asyncio.gather(consume_dispatch_ready(), consume_manager_alerts(), consume_return_matched())
    finally:
        await _APP.updater.stop()
        await _APP.stop()
        await _APP.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
