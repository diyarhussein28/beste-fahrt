"""Manager-only Telegram commands: onboard/offboard drivers without ever
touching a terminal. Every manager command checks the caller's chat_id
against TELEGRAM_MANAGER_CHAT_ID — the same whitelist principle القسم 10
applies to drivers applies here to the control surface itself, so only the
person holding that one chat_id can add, remove, or reconfigure anyone.

Onboarding flow: /add_driver creates the driver row and a one-time invite
link (t.me/<bot>?start=<code>); the new hire taps it, /start consumes the
code and links their chat_id — no need to know their chat_id up front.
"""
from __future__ import annotations

import logging
import re
import secrets as _pysecrets

from telegram import Update
from telegram.ext import ContextTypes

from shared.config import get_secrets
from shared.db import (
    add_driver,
    consume_driver_invite,
    create_driver_invite,
    find_driver_by_name,
    list_drivers,
    set_driver_active,
    set_driver_consent,
    set_driver_home,
)

log = logging.getLogger("dispatcher.manager_commands")

_COORD_RE = re.compile(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)")


def _is_manager(update: Update) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    manager_id = get_secrets().telegram_manager_chat_id
    return bool(manager_id) and chat_id == manager_id


async def _reject(update: Update) -> None:
    if update.message:
        await update.message.reply_text("هذا الأمر مخصّص للمدير فقط.")
    log.warning(
        "manager command attempted by non-manager chat_id",
        extra={"extra_fields": {"chat_id": update.effective_chat.id if update.effective_chat else None}},
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return

    if context.args:
        driver = await consume_driver_invite(context.args[0], chat_id)
        if driver is None:
            await update.message.reply_text("رابط الدعوة غير صالح أو منتهي الصلاحية. اطلب رابطاً جديداً من مديرك.")
            return
        await update.message.reply_text(
            f"أهلاً {driver.name}! تم ربط حسابك بالنظام بنجاح.\n\n"
            "لتفعيل مشاركة موقعك أثناء العمل (اختياري لكنه يحسّن دقة التوجيه) أرسل /consent_on\n"
            "ستصلك عروض النقل المناسبة هنا فور توفّرها."
        )
        secrets = get_secrets()
        if secrets.telegram_manager_chat_id:
            await context.bot.send_message(secrets.telegram_manager_chat_id, f"🔗 {driver.name} انضم وربط حسابه بنجاح.")
        return

    await update.message.reply_text("مرحباً بك في بوت Fleet Dispatch Monitor 🚚")


async def cmd_consent_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return
    if await set_driver_consent(chat_id, True):
        await update.message.reply_text(
            "✅ تم تفعيل مشاركة الموقع. شارك موقعك المباشر من تيليغرام "
            "(📎 → Location → Share Live Location) أثناء ساعات العمل فقط."
        )
    else:
        await update.message.reply_text("لم يتم العثور على حسابك كسائق مسجّل — اطلب رابط انضمام من مديرك أولاً.")


async def cmd_consent_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return
    if await set_driver_consent(chat_id, False):
        await update.message.reply_text("تم إيقاف مشاركة الموقع.")
    else:
        await update.message.reply_text("لم يتم العثور على حسابك.")


async def cmd_drivers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)
    drivers = await list_drivers()
    if not drivers:
        await update.message.reply_text("لا يوجد سائقون بعد. أضف أول سائق بـ:\n/add_driver <الاسم>")
        return
    lines = ["📋 السائقون:"]
    for d in drivers:
        state = "نشط" if d.active else "🚫 موقوف"
        linked = "مرتبط" if d.telegram_chat_id else "بانتظار الربط"
        consent = "✅" if d.location_consent else "—"
        lines.append(f"• {d.name} — {d.status.value} — {state} — {linked} — موقع:{consent}")
    await update.message.reply_text("\n".join(lines))


async def cmd_add_driver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)
    if not context.args:
        await update.message.reply_text("الاستخدام: /add_driver <الاسم>\nمثال: /add_driver Ahmed K.")
        return

    name = " ".join(context.args)
    if await find_driver_by_name(name) is not None:
        await update.message.reply_text(f"يوجد سائق بهذا الاسم بالفعل. راجع /drivers")
        return

    driver = await add_driver(name)
    code = _pysecrets.token_urlsafe(6)
    await create_driver_invite(driver.id, code)
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={code}"
    await update.message.reply_text(
        f"✅ تمت إضافة {name}.\n\n"
        f"أرسل له هذا الرابط ليضغط \"Start\" فيرتبط حسابه تلقائياً (صالح 7 أيام):\n{link}\n\n"
        f"بعدها اضبط منزله الأساسي (مطلوب لبحث رحلة العودة):\n"
        f"/set_home {name} 51.0459,7.0192 Leverkusen"
    )


async def cmd_remove_driver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)
    if not context.args:
        await update.message.reply_text("الاستخدام: /remove_driver <الاسم>")
        return

    name = " ".join(context.args)
    driver = await find_driver_by_name(name)
    if driver is None:
        await update.message.reply_text(f"لا يوجد سائق باسم '{name}'. تحقق من /drivers")
        return

    await set_driver_active(driver.id, False)
    await update.message.reply_text(
        f"🚫 تم إيقاف {name} — لن يصله أي عرض جديد بعد الآن.\n"
        f"(سجلّه وتاريخه محفوظان، ويمكن إعادة تفعيله لاحقاً بـ /activate_driver {name})"
    )


async def cmd_activate_driver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)
    if not context.args:
        await update.message.reply_text("الاستخدام: /activate_driver <الاسم>")
        return

    name = " ".join(context.args)
    driver = await find_driver_by_name(name)
    if driver is None:
        await update.message.reply_text(f"لا يوجد سائق باسم '{name}'.")
        return

    await set_driver_active(driver.id, True)
    await update.message.reply_text(f"✅ تم تفعيل {name} من جديد.")


async def cmd_set_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)

    raw = " ".join(context.args)
    match = _COORD_RE.search(raw)
    if not match:
        await update.message.reply_text(
            "الاستخدام: /set_home <الاسم> <lat>,<lon> [المدينة]\n"
            "مثال: /set_home Ahmed K. 51.0459,7.0192 Leverkusen"
        )
        return

    name = raw[: match.start()].strip()
    city = raw[match.end():].strip() or None
    driver = await find_driver_by_name(name)
    if driver is None:
        await update.message.reply_text(f"لا يوجد سائق باسم '{name}'. تحقق من /drivers")
        return

    lat, lon = float(match.group(1)), float(match.group(2))
    await set_driver_home(driver.id, lat, lon, city)
    await update.message.reply_text(f"✅ تم ضبط منزل {name} على ({lat}, {lon}) {city or ''}".strip())
