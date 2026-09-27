"""Message templates — القسم 7.3 و8.10. Pure string-building, no Telegram
import here, so templates can be unit-tested without python-telegram-bot.

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
    return f"{value:.1f} كم" if value is not None else "—"


def render_offer_alert(job: Job, approach_km: float, privacy: PrivacyConfig) -> tuple[str, Buttons]:
    """قالب عرض عادي — القسم 7.3."""
    epk = eur_per_km(job.price_eur, approach_km, job.route_km)
    date_str = job.pickup_date.strftime("%d.%m.%Y – %H:%M") if job.pickup_date else "—"
    lines = [
        f"🚗 عرض جديد — المسافة إليك: {_fmt_km(approach_km)} | طول الرحلة: {_fmt_km(job.route_km)}",
        f"📍 استلام:  {redact_address(job.pickup_addr, privacy)}",
        f"🏁 تسليم:   {redact_address(job.dropoff_addr, privacy)}",
        f"📅 الموعد:  {date_str}",
        f"💶 السعر:   {_fmt_price(job.price_eur)}   |   ربحية: {epk:.2f} €/كم" if epk else f"💶 السعر:   {_fmt_price(job.price_eur)}",
    ]
    if job.url:
        lines.append(f"🔗 فتح العرض: {job.url}")
    text = "\n".join(lines)
    buttons: Buttons = [[("✅ سآخذها", f"acc:{job.fp}"), ("❌ لا أستطيع", f"dec:{job.fp}")]]
    return text, buttons


def render_combined_alert(
    outbound: Job, approach_km: float, ret: ReturnCandidate, privacy: PrivacyConfig
) -> tuple[str, Buttons]:
    """عرض ذهاب مع عودة متاحة وقت ظهوره — القسم 8.10 (التنبيه الأول)."""
    round_trip_epk = (outbound.price_eur or 0) + (ret.job.price_eur or 0)
    total_km = (outbound.route_km or 0) + ret.deadhead_km + (ret.job.route_km or 0)
    round_trip_per_km = round_trip_epk / total_km if total_km else None

    lines = [
        f"🚗 عرض ذهاب: {redact_address(outbound.pickup_addr, privacy)} → {redact_address(outbound.dropoff_addr, privacy)}"
        f"  |  {_fmt_km(outbound.route_km)}  |  {_fmt_price(outbound.price_eur)}",
        f"🔁 عودة متاحة: {redact_address(ret.job.pickup_addr, privacy)} → {redact_address(ret.job.dropoff_addr, privacy)}"
        f"  |  {_fmt_km(ret.job.route_km)}  |  {_fmt_price(ret.job.price_eur)}",
        f"   المسافة من التسليم: {_fmt_km(ret.deadhead_km)}",
        f"💶 إجمالي الرحلتين: {_fmt_price(round_trip_epk)}"
        + (f"  |  ربحية الجولة: {round_trip_per_km:.2f} €/كم" if round_trip_per_km else ""),
    ]
    if outbound.url:
        lines.append(f"🔗 الذهاب: {outbound.url}")
    if ret.job.url:
        lines.append(f"🔗 العودة: {ret.job.url}")
    text = "\n".join(lines)
    buttons: Buttons = [
        [
            ("✅ آخذ الاثنتين", f"accboth:{outbound.fp}:{ret.job.fp}"),
            ("➡️ الذهاب فقط", f"acc:{outbound.fp}"),
            ("❌ لا", f"dec:{outbound.fp}"),
        ]
    ]
    return text, buttons


def render_return_alert(
    ret: ReturnCandidate, hours_after_eta: float, km_from_home: float, privacy: PrivacyConfig
) -> tuple[str, Buttons]:
    """تنبيه عودة لاحق لصاحب طلب العودة المفتوح فقط — القسم 8.10 (التنبيه الثاني)."""
    date_str = ret.job.pickup_date.strftime("%d.%m.%Y – %H:%M") if ret.job.pickup_date else "—"
    lines = [
        "🔁 رحلة عودة لك الآن!",
        f"📍 من: {redact_address(ret.job.pickup_addr, privacy)} ({_fmt_km(ret.deadhead_km)} من مكان تسليمك)",
        f"🏁 إلى: {redact_address(ret.job.dropoff_addr, privacy)} ({_fmt_km(km_from_home)} من منزلك)",
        f"📅 الاستلام: {date_str}  (بعد وصولك بـ {hours_after_eta:.1f} ساعة)",
        f"💶 {_fmt_price(ret.job.price_eur)}",
    ]
    if ret.job.url:
        lines.append(f"🔗 {ret.job.url}")
    text = "\n".join(lines)
    buttons: Buttons = [
        [
            ("✅ سآخذها", f"acc:{ret.job.fp}"),
            ("⏭️ ابحث عن غيرها", f"skip_return:{ret.job.fp}"),
            ("🚆 سأعود بالقطار", f"declare_train:{ret.job.fp}"),
        ]
    ]
    return text, buttons


def render_manager_escalation(job: Job, attempts: int) -> str:
    """ملخص يُرسل للمدير بعد استنفاد المحاولات — القسم 7.2."""
    return (
        f"⚠️ لم يقبل أي سائق العرض بعد {attempts} محاولات.\n"
        f"📍 {job.pickup_addr} → {job.dropoff_addr}\n"
        f"💶 {_fmt_price(job.price_eur)}  |  {_fmt_km(job.route_km)}\n"
        + (f"🔗 {job.url}" if job.url else "")
    )


def render_manager_text_alert(message: str) -> str:
    """تنبيهات تشغيلية عامة (CAPTCHA/2FA، تقييد المعدل، توقف تحليل الصفحة...)."""
    return f"⚠️ {message}"
