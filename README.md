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

## إدارة السائقين (من داخل Telegram — أنت فقط)

كل أوامر الإدارة محصورة بصاحب `TELEGRAM_MANAGER_CHAT_ID` في `.env` — أي
شخص آخر يكتبها يحصل على "هذا الأمر مخصّص للمدير فقط" ولا يتم تنفيذ شيء
([`dispatcher/manager_commands.py`](dispatcher/manager_commands.py)).

| الأمر | الوظيفة |
|---|---|
| `/drivers` | قائمة كل السائقين وحالتهم |
| `/add_driver <الاسم>` | يضيف سائقاً جديداً ويرسل لك رابط دعوة تبعثه له |
| `/remove_driver <الاسم>` | يوقف سائقاً فوراً (لا يحذف سجلّه/تاريخه) |
| `/activate_driver <الاسم>` | يعيد تفعيل سائق موقوف |
| `/set_home <الاسم> <lat>,<lon> [مدينة]` | يضبط منزل السائق (ضروري لرحلة العودة) |

**تدفّق توظيف سائق جديد:**
1. تكتب `/add_driver Ahmed K.` → يردّ عليك البوت برابط دعوة.
2. تبعث الرابط للسائق الجديد على تيليغرام، يضغط "Start" فيرتبط حسابه تلقائياً.
3. تضبط منزله: `/set_home Ahmed K. 51.0459,7.0192 Leverkusen`.
4. السائق نفسه يرسل `/consent_on` لتفعيل مشاركة موقعه المباشر (اختياري لكنه يحسّن الدقة).

**عندما ينتهي عمل سائق معك:** `/remove_driver Ahmed K.` — يتوقف فوراً عن
استقبال أي عرض جديد.

للبيانات التجريبية فقط (اختبار محلي بدون سائقين حقيقيين):
```bash
docker compose exec collector python -m scripts.seed_demo_drivers
```
هناك أيضاً [`scripts/manage_driver.py`](scripts/manage_driver.py) كأداة
سطر أوامر بديلة لنفس العمليات إن احتجتها من الخادم مباشرة.

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
