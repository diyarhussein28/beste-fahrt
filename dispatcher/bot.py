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
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from shared.config import get_config, get_secrets
from shared.db import get_driver_by_chat_id, record_dispatch_response, try_claim_job
from shared.events import ack, consume
from shared.logging_config import configure_logging
from shared.models import JobStatus, RankedDriver

from matcher.service import rank_job
from dispatcher import escalation, templates

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
    if driver is None:
        # القسم 10: قائمة بيضاء بـ chat_id — تجاهل أي رسالة من غير سائق مسجّل.
        log.warning("callback from unknown chat_id ignored", extra={"extra_fields": {"chat_id": chat_id}})
        await query.answer("غير مصرح")
        return

    action, _, rest = query.data.partition(":")
    job_fp = rest.split(":")[0]

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

    elif action == "dec":
        await record_dispatch_response(job_fp, driver.id, "decline")
        await query.answer("تم تسجيل الرفض")
        await query.edit_message_text(query.message.text + "\n\n❌ تم الرفض")

    elif action in ("skip_return", "declare_train"):
        # Full return-watch handling lives in returns/watches.py — this just
        # acknowledges the tap so the driver isn't left hanging.
        await query.answer("تم التسجيل")

    else:
        log.warning("unknown callback action", extra={"extra_fields": {"data": query.data}})
        await query.answer()


async def send_offer_to_driver(candidate: RankedDriver, job, rank: int) -> None:
    privacy = get_config().privacy
    text, buttons = templates.render_offer_alert(job, candidate.approach_km, privacy)
    app: Application = _APP
    await app.bot.send_message(
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


_APP: Application


async def run() -> None:
    global _APP
    configure_logging()
    secrets = get_secrets()
    if not secrets.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    _APP = Application.builder().token(secrets.telegram_bot_token).build()
    _APP.add_handler(CallbackQueryHandler(on_callback))

    await _APP.initialize()
    await _APP.start()
    await _APP.updater.start_polling()
    log.info("dispatcher bot started")
    try:
        await asyncio.gather(consume_dispatch_ready(), consume_manager_alerts())
    finally:
        await _APP.updater.stop()
        await _APP.stop()
        await _APP.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
