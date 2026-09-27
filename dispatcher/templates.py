"""Message templates — القسم 7.3 و8.10. Pure string-building, no Telegram
import here, so templates can be unit-tested without python-telegram-bot.

Every user-facing string here is German (the bot's operating language, per
the manager's request) — only comments/docstrings referencing the spec
sections stay Arabic since those are internal documentation, not something
a driver or manager ever sees.

Each render_* function returns (text, buttons), where buttons is a list of
rows of (label, callback_data) — dispatcher/bot.py turns that into an
InlineKeyboardMarkup.
"""
from __future__ import annotations

from shared.config import PrivacyConfig
from shared.models import Job, ReturnCandidate
from matcher.engine import eur_per_km

Button = tuple[str, str]
Buttons = list[list[Button]]


def redact_address(address: str, privacy: PrivacyConfig) -> str:
    """القسم 11.3: تقليل البيانات — المدينة بدل العنوان الكامل حين تكون
    الخصوصية مفعّلة."""
    if privacy.show_full_address_in_alert:
        return address
    return address.split(",")[0].strip()


def _fmt_price(value: float | None) -> str:
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".") if value is not None else "—"


def _fmt_km(value: float | None) -> str:
    return f"{value:.1f} km" if value is not None else "—"


def render_offer_alert(job: Job, approach_km: float, privacy: PrivacyConfig) -> tuple[str, Buttons]:
    """قالب عرض عادي — القسم 7.3."""
    epk = eur_per_km(job.price_eur, approach_km, job.route_km)
    date_str = job.pickup_date.strftime("%d.%m.%Y – %H:%M") if job.pickup_date else "—"
    lines = [
        f"🚗 Neuer Auftrag — Entfernung zu dir: {_fmt_km(approach_km)} | Streckenlänge: {_fmt_km(job.route_km)}",
        f"📍 Abholung:  {redact_address(job.pickup_addr, privacy)}",
        f"🏁 Lieferung: {redact_address(job.dropoff_addr, privacy)}",
        f"📅 Termin:    {date_str}",
        f"💶 Preis:     {_fmt_price(job.price_eur)}   |   Rentabilität: {epk:.2f} €/km" if epk else f"💶 Preis:     {_fmt_price(job.price_eur)}",
    ]
    if job.url:
        lines.append(f"🔗 Auftrag öffnen: {job.url}")
    text = "\n".join(lines)
    buttons: Buttons = [[("✅ Ich nehme ihn an", f"acc:{job.fp}"), ("❌ Kann ich nicht", f"dec:{job.fp}")]]
    return text, buttons


def render_combined_alert(
    outbound: Job, approach_km: float, ret: ReturnCandidate, privacy: PrivacyConfig
) -> tuple[str, Buttons]:
    """عرض ذهاب مع عودة متاحة وقت ظهوره — القسم 8.10 (التنبيه الأول)."""
    round_trip_epk = (outbound.price_eur or 0) + (ret.job.price_eur or 0)
    total_km = (outbound.route_km or 0) + ret.deadhead_km + (ret.job.route_km or 0)
    round_trip_per_km = round_trip_epk / total_km if total_km else None

    lines = [
        f"🚗 Hinfahrt: {redact_address(outbound.pickup_addr, privacy)} → {redact_address(outbound.dropoff_addr, privacy)}"
        f"  |  {_fmt_km(outbound.route_km)}  |  {_fmt_price(outbound.price_eur)}",
        f"🔁 Rückfahrt verfügbar: {redact_address(ret.job.pickup_addr, privacy)} → {redact_address(ret.job.dropoff_addr, privacy)}"
        f"  |  {_fmt_km(ret.job.route_km)}  |  {_fmt_price(ret.job.price_eur)}",
        f"   Entfernung ab Lieferort: {_fmt_km(ret.deadhead_km)}",
        f"💶 Beide Fahrten gesamt: {_fmt_price(round_trip_epk)}"
        + (f"  |  Tour-Rentabilität: {round_trip_per_km:.2f} €/km" if round_trip_per_km else ""),
    ]
    if outbound.url:
        lines.append(f"🔗 Hinfahrt: {outbound.url}")
    if ret.job.url:
        lines.append(f"🔗 Rückfahrt: {ret.job.url}")
    text = "\n".join(lines)
    buttons: Buttons = [
        [
            ("✅ Beide nehmen", f"accboth:{outbound.fp}:{ret.job.fp}"),
            ("➡️ Nur Hinfahrt", f"acc:{outbound.fp}"),
            ("❌ Nein", f"dec:{outbound.fp}"),
        ]
    ]
    return text, buttons


def render_return_alert(
    ret: ReturnCandidate, hours_after_eta: float, km_from_home: float, privacy: PrivacyConfig, watch_id: int
) -> tuple[str, Buttons]:
    """تنبيه عودة لاحق لصاحب طلب العودة المفتوح فقط — القسم 8.10 (التنبيه الثاني)."""
    date_str = ret.job.pickup_date.strftime("%d.%m.%Y – %H:%M") if ret.job.pickup_date else "—"
    lines = [
        "🔁 Jetzt eine Rückfahrt für dich verfügbar!",
        f"📍 Von: {redact_address(ret.job.pickup_addr, privacy)} ({_fmt_km(ret.deadhead_km)} von deinem Lieferort)",
        f"🏁 Nach: {redact_address(ret.job.dropoff_addr, privacy)} ({_fmt_km(km_from_home)} von zu Hause)",
        f"📅 Abholung: {date_str}  ({hours_after_eta:.1f} Std. nach deiner Ankunft)",
        f"💶 {_fmt_price(ret.job.price_eur)}",
    ]
    if ret.job.url:
        lines.append(f"🔗 {ret.job.url}")
    text = "\n".join(lines)
    buttons: Buttons = [
        [
            ("✅ Ich nehme sie an", f"acc:{ret.job.fp}"),
            ("⏭️ Andere suchen", f"skip_return:{ret.job.fp}:{watch_id}"),
            ("🚆 Ich fahre mit dem Zug zurück", f"declare_train:{ret.job.fp}:{watch_id}"),
        ]
    ]
    return text, buttons


def render_manager_escalation(job: Job, attempts: int) -> str:
    """ملخص يُرسل للمدير بعد استنفاد المحاولات — القسم 7.2."""
    return (
        f"⚠️ Kein Fahrer hat den Auftrag nach {attempts} Versuchen angenommen.\n"
        f"📍 {job.pickup_addr} → {job.dropoff_addr}\n"
        f"💶 {_fmt_price(job.price_eur)}  |  {_fmt_km(job.route_km)}\n"
        + (f"🔗 {job.url}" if job.url else "")
    )


def render_manager_text_alert(message: str) -> str:
    """تنبيهات تشغيلية عامة (CAPTCHA/2FA، تقييد المعدل، توقف تحليل الصفحة...)."""
    return f"⚠️ {message}"
