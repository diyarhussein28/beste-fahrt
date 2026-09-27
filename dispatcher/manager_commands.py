"""Manager-only Telegram commands: onboard/offboard drivers without ever
touching a terminal. Every manager command checks the caller's chat_id
against TELEGRAM_MANAGER_CHAT_ID — the same whitelist principle القسم 10
applies to drivers applies here to the control surface itself, so only the
person holding that one chat_id can add, remove, or reconfigure anyone.

All user-facing text is German (the bot's operating language); only
comments/docstrings referencing spec sections stay Arabic since those are
internal documentation, never shown to a driver or manager.

Onboarding flow: /add_driver creates the driver row and a one-time invite
link (t.me/<bot>?start=<code>); the new hire taps it, /start consumes the
code and links their chat_id — no need to know their chat_id up front.
"""
from __future__ import annotations

import logging
import secrets as _pysecrets

from telegram import Update
from telegram.ext import ContextTypes

from shared.config import get_secrets
from shared.models import DriverStatus
from shared.db import (
    add_driver,
    consume_driver_invite,
    create_driver_invite,
    find_driver_by_name,
    get_driver_by_chat_id,
    list_drivers,
    record_driver_location,
    set_driver_active,
    set_driver_consent,
    set_driver_home,
    set_driver_shift_status,
)
from collector.geo import geocode

log = logging.getLogger("dispatcher.manager_commands")


def _is_manager(update: Update) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    manager_id = get_secrets().telegram_manager_chat_id
    return bool(manager_id) and chat_id == manager_id


async def _reject(update: Update) -> None:
    if update.message:
        await update.message.reply_text("Dieser Befehl ist nur für den Manager.")
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
            await update.message.reply_text(
                "Der Einladungslink ist ungültig oder abgelaufen. Bitte einen neuen Link vom Manager anfordern."
            )
            return
        await update.message.reply_text(
            f"Willkommen, {driver.name}! Dein Konto wurde erfolgreich verknüpft.\n\n"
            "Sobald du im Dienst bist, sende /available, damit dir Aufträge zugeschickt werden.\n"
            "Setze deinen Heimatort mit /home <Ort> (nötig für Rückfahrten).\n"
            "Für Live-Standortfreigabe (verbessert die Zuordnung) sende /consent_on."
        )
        secrets = get_secrets()
        if secrets.telegram_manager_chat_id:
            await context.bot.send_message(
                secrets.telegram_manager_chat_id, f"🔗 {driver.name} ist beigetreten und hat sein Konto verknüpft."
            )
        return

    await update.message.reply_text("Willkommen beim Fleet Dispatch Monitor Bot 🚚")


async def cmd_consent_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return
    if await set_driver_consent(chat_id, True):
        await update.message.reply_text(
            "✅ Standortfreigabe aktiviert. Teile deinen Live-Standort über Telegram "
            "(📎 → Standort → Live-Standort teilen) nur während der Arbeitszeit."
        )
    else:
        await update.message.reply_text("Kein registriertes Fahrerkonto gefunden — bitte zuerst einen Einladungslink vom Manager anfordern.")


async def cmd_consent_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return
    if await set_driver_consent(chat_id, False):
        await update.message.reply_text("Standortfreigabe deaktiviert.")
    else:
        await update.message.reply_text("Kein Konto gefunden.")


async def cmd_available(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fahrer meldet sich im Dienst — erst danach kommen Auftragsangebote an."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return
    if await set_driver_shift_status(chat_id, DriverStatus.AVAILABLE):
        await update.message.reply_text("✅ Du bist jetzt verfügbar. Neue Aufträge in deiner Nähe werden dir zugeschickt.")
    else:
        await update.message.reply_text("Kein aktives Fahrerkonto gefunden — bitte zuerst über einen Einladungslink verknüpfen.")


async def cmd_offline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return
    if await set_driver_shift_status(chat_id, DriverStatus.OFF_DUTY):
        await update.message.reply_text("Du bist jetzt offline. Dir werden keine neuen Aufträge mehr zugeschickt.")
    else:
        await update.message.reply_text("Kein Konto gefunden.")


async def cmd_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manuelle Standorteingabe — القسم 6.1's dritte, schwächste Quelle
    ('إدخال يدوي'), als Fallback wenn Live-Standort nicht geteilt wird.
    Takes a plain place name (geocoded via Nominatim, same as an offer's
    pickup/dropoff address), not raw coordinates — nobody driving a car
    should need to go find their own lat/lon first.
    """
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return

    driver = await get_driver_by_chat_id(chat_id)
    if driver is None or not driver.active:
        await update.message.reply_text("Kein aktives Fahrerkonto gefunden — bitte zuerst über einen Einladungslink verknüpfen.")
        return

    address = " ".join(context.args).strip()
    if not address:
        await update.message.reply_text("Verwendung: /location <Ort>\nBeispiel: /location Leverkusen-Opladen")
        return

    result = await geocode(address)
    if result is None:
        await update.message.reply_text(f"Ort '{address}' konnte nicht gefunden werden — bitte genauer angeben (z.B. mit Stadt).")
        return

    lat, lon = result
    await record_driver_location(driver.id, lat, lon, source="manual")
    await update.message.reply_text(f"✅ Standort aktualisiert: {address}")


async def cmd_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fahrer setzt seinen eigenen Heimatort — nötig für die Rückfahrtsuche
    (القسم 8). Nur der Fahrer selbst oder der Manager (/set_home) kann das.
    """
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or update.message is None:
        return

    driver = await get_driver_by_chat_id(chat_id)
    if driver is None or not driver.active:
        await update.message.reply_text("Kein aktives Fahrerkonto gefunden — bitte zuerst über einen Einladungslink verknüpfen.")
        return

    address = " ".join(context.args).strip()
    if not address:
        await update.message.reply_text("Verwendung: /home <Ort>\nBeispiel: /home Leverkusen")
        return

    result = await geocode(address)
    if result is None:
        await update.message.reply_text(f"Ort '{address}' konnte nicht gefunden werden — bitte genauer angeben (z.B. mit Stadt).")
        return

    lat, lon = result
    await set_driver_home(driver.id, lat, lon, address)
    await update.message.reply_text(f"✅ Heimatort gesetzt auf: {address}")


async def cmd_drivers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)
    drivers = await list_drivers()
    if not drivers:
        await update.message.reply_text("Noch keine Fahrer vorhanden. Füge den ersten hinzu mit:\n/add_driver <Name>")
        return
    lines = ["📋 Fahrer:"]
    for d in drivers:
        state = "aktiv" if d.active else "🚫 gesperrt"
        linked = "verknüpft" if d.telegram_chat_id else "wartet auf Verknüpfung"
        consent = "✅" if d.location_consent else "—"
        lines.append(f"• {d.name} — {d.status.value} — {state} — {linked} — Standort:{consent}")
    await update.message.reply_text("\n".join(lines))


async def cmd_add_driver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)
    if not context.args:
        await update.message.reply_text("Verwendung: /add_driver <Name>\nBeispiel: /add_driver Ahmed K.")
        return

    name = " ".join(context.args)
    if await find_driver_by_name(name) is not None:
        await update.message.reply_text("Ein Fahrer mit diesem Namen existiert bereits. Siehe /drivers")
        return

    driver = await add_driver(name)
    code = _pysecrets.token_urlsafe(6)
    await create_driver_invite(driver.id, code)
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={code}"
    await update.message.reply_text(
        f"✅ {name} wurde hinzugefügt.\n\n"
        f"Sende ihm diesen Link — ein Tap auf \"Start\" verknüpft sein Konto automatisch (7 Tage gültig):\n{link}\n\n"
        f"Danach seinen Heimatort festlegen (nötig für die Rückfahrtsuche) — "
        f"entweder du:\n/set_home {name}, <Ort>\n"
        f"oder er selbst nach dem Verknüpfen mit:\n/home <Ort>"
    )


async def cmd_remove_driver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)
    if not context.args:
        await update.message.reply_text("Verwendung: /remove_driver <Name>")
        return

    name = " ".join(context.args)
    driver = await find_driver_by_name(name)
    if driver is None:
        await update.message.reply_text(f"Kein Fahrer namens '{name}' gefunden. Siehe /drivers")
        return

    await set_driver_active(driver.id, False)
    await update.message.reply_text(
        f"🚫 {name} wurde gesperrt — erhält ab sofort keine neuen Aufträge mehr.\n"
        f"(Verlauf/Daten bleiben erhalten; Reaktivierung mit /activate_driver {name})"
    )


async def cmd_activate_driver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_manager(update):
        return await _reject(update)
    if not context.args:
        await update.message.reply_text("Verwendung: /activate_driver <Name>")
        return

    name = " ".join(context.args)
    driver = await find_driver_by_name(name)
    if driver is None:
        await update.message.reply_text(f"Kein Fahrer namens '{name}' gefunden.")
        return

    await set_driver_active(driver.id, True)
    await update.message.reply_text(f"✅ {name} wurde wieder aktiviert.")


async def cmd_set_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verwendung: /set_home <Name>, <Ort> — das Komma trennt Name und Ort,
    da beide aus mehreren Wörtern bestehen können (z.B. "Ahmed K." und
    "Bergisch Gladbach"). Der Ort wird wie eine Auftragsadresse geocodiert.
    """
    if not _is_manager(update):
        return await _reject(update)

    raw = " ".join(context.args)
    if "," not in raw:
        await update.message.reply_text(
            "Verwendung: /set_home <Name>, <Ort>\nBeispiel: /set_home Ahmed K., Leverkusen"
        )
        return

    name, _, address = raw.partition(",")
    name, address = name.strip(), address.strip()
    if not name or not address:
        await update.message.reply_text(
            "Verwendung: /set_home <Name>, <Ort>\nBeispiel: /set_home Ahmed K., Leverkusen"
        )
        return

    driver = await find_driver_by_name(name)
    if driver is None:
        await update.message.reply_text(f"Kein Fahrer namens '{name}' gefunden. Siehe /drivers")
        return

    result = await geocode(address)
    if result is None:
        await update.message.reply_text(f"Ort '{address}' konnte nicht gefunden werden — bitte genauer angeben (z.B. mit Stadt).")
        return

    lat, lon = result
    await set_driver_home(driver.id, lat, lon, address)
    await update.message.reply_text(f"✅ Heimatort von {name} gesetzt auf: {address}")
