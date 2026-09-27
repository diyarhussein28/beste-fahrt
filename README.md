# Fleet Dispatch Monitor — Read-Only Bot

[![tests](https://github.com/diyarhussein28/beste-fahrt/actions/workflows/tests.yml/badge.svg)](https://github.com/diyarhussein28/beste-fahrt/actions/workflows/tests.yml)

نظام مراقبة وتوجيه ذكي لأسطول نقل السيارات: يقرأ عروض النقل الجديدة من منصة
الأسطول، يطابقها مع أقرب سائق متاح، ويرسل تنبيهاً فورياً عبر Telegram —
**بدون أي حجز آلي**. الحجز نفسه دائماً قرار بشري بضغطة زر. يبحث النظام أيضاً
عن رحلة عودة مدفوعة تعيد السائق إلى منزله بدل أن يعود فارغاً.

راجع الوثيقة التقنية الكاملة لتفاصيل التصميم والمعمارية والقرارات.

## المبادئ الأساسية

- **قراءة فقط**: لا حجز ولا قبول ولا رفض آلي على منصة الأسطول.
- **الإنسان يقرّر**: كل حجز يتم يدوياً من السائق أو المدير في التطبيق الرسمي.
- **سلوك معتدل**: استعلام دوري بطيء (~30 ثانية) مع تذبذب عشوائي، يتوقف عند
  CAPTCHA أو 2FA وينبّه المدير بدل تجاوز الحماية.
- **خصوصية السائقين**: تتبّع الموقع بموافقة صريحة وخلال ساعات العمل فقط،
  مع مدة احتفاظ محدودة بالبيانات.

## المكوّنات

| المكوّن | المجلد | المسؤولية |
|---|---|---|
| Collector | [`collector/`](collector/) | تسجيل الدخول، الحفاظ على الجلسة، قراءة العروض دورياً |
| Matching Engine | [`matcher/`](matcher/) | حساب المسافة وترتيب السائقين حسب الأولوية |
| Return Trip Finder | [`returns/`](returns/) | البحث عن رحلة عودة مدفوعة للسائق |
| Dispatcher | [`dispatcher/`](dispatcher/) | إرسال التنبيهات عبر Telegram، التصعيد |
| Admin Panel | [`admin/`](admin/) | لوحة بسيطة لحالة السائقين والعروض |
| Shared | [`shared/`](shared/) | الإعدادات، النماذج، الوصول لقاعدة البيانات، طابور الأحداث |
| Migrations | [`migrations/`](migrations/) | مخطط قاعدة البيانات (PostgreSQL + PostGIS) |
| Ops | [`ops/`](ops/) | النبضات، النسخ الاحتياطي، التقارير الأسبوعية |
| Scripts | [`scripts/`](scripts/) | أدوات سطر أوامر لإدارة السائقين وبيانات تجريبية |

## البدء السريع (تطوير محلي)

```bash
cp .env.example .env        # عدّل القيم
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium # فقط إذا كنت ستشغّل Collector فعلياً

pytest                       # اختبارات الوحدة (لا تحتاج قاعدة بيانات)
```

## النشر (إنتاج)

```bash
cp .env.example .env         # عدّل القيم الحقيقية، ولا ترفعه لـ Git أبداً
docker compose up -d --build
```

`docker-compose.yml` يشغّل: `db` (PostgreSQL+PostGIS)، `redis`، `migrate`
(يُطبَّق مرة عند الإقلاع)، ثم `collector`، `matcher`، `returns`،
`dispatcher`، `admin`، و`ops` (النبضات + التقرير الأسبوعي).

## إعداد السائقين

لا يوجد نموذج ويب لإضافة سائقين بعد (اللوحة قراءة فقط عمداً) — استخدم:

```bash
# بيانات تجريبية لعشرة سائقين حول Leverkusen (للتطوير/العرض فقط)
docker compose exec collector python -m scripts.seed_demo_drivers

# ربط سائق حقيقي بحسابه على Telegram (chat_id يظهر أول مرة يراسل فيها البوت)
docker compose exec collector python -m scripts.manage_driver link-telegram "Ahmed K." 123456789
docker compose exec collector python -m scripts.manage_driver set-home "Ahmed K." 51.0459 7.0192 --city Leverkusen
docker compose exec collector python -m scripts.manage_driver set-consent "Ahmed K." on
```

راجع [`scripts/manage_driver.py`](scripts/manage_driver.py) لباقي الأوامر
(الرخص، إلغاء التفعيل...).

## لوحة الإدارة والتقارير

- اللوحة: `http://<server>:8080` (HTTP Basic — المستخدم `admin`، كلمة المرور
  `ADMIN_SECRET_KEY` من `.env`). تعرض حالة السائقين، العروض المفتوحة، آخر
  التنبيهات، ومؤشرات آخر 7 أيام (القسم 15).
- تقرير أسبوعي تلقائي يُرسل لقناة المدير على Telegram كل إثنين 08:00
  ([`ops/weekly_report.py`](ops/weekly_report.py)).

## ربط منصة حقيقية

الـ Collector لا يعرف تفاصيل أي منصة تحديداً — راجع
[`collector/parser.py`](collector/parser.py) لواجهة `OfferParser`
و[`collector/example_parser.py`](collector/example_parser.py) لمثال. عدّل
`config.yaml` (روابط، محددات CSS) وأضف parser خاص بمنصتك قبل التشغيل الفعلي.

## ملاحظة قانونية

هذا المشروع **للاستخدام الداخلي على حساب الأسطول الخاص بك فقط**. قبل
التشغيل الفعلي: راجع شروط استخدام المنصة، اسأل عن API رسمي أولاً، ولا تتجاوز
أي حماية تقنية (CAPTCHA، 2FA). هذا ليس استشارة قانونية — استشر مختصاً قبل
الإطلاق. التفاصيل الكاملة في القسم 11 من الوثيقة التقنية.
