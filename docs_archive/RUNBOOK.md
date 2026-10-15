> **Архив. Не обновляется.** Техчасть актуальна на 14.10; живой журнал — `AGENT.md`.

# RUNBOOK — полное описание проекта «Găsește Casa / ChirieBot»

**Зачем этот файл:** чтобы проект можно было восстановить с нуля **только по нему** — на новом сервере,
после потери кода, после смерти ноутбука, или через полгода, когда всё забылось.
Секретов здесь нет: токены, пароли и IP — в `ACCESS.md` (не в git).

Связанные документы: `MISSION.md` (правила и автономность агента), `STATUS.md` (снятие состояния
«что работает / что нет» на сегодня), `GROWTH.md` (наполнение доски, тексты, цены, платёжные рельсы).

---

## 1. Что это за продукт

Маркетплейс аренды и продажи недвижимости Кишинёва **внутри Telegram**:

- **Telegram-бот** (`@gaseste_casa_bot`) — поиск с фильтрами, подача объявления (бесплатно),
  модерация, «мои объявления», платный **Boost**.
- **Публичный канал** `t.me/gaseste_casa` — бот сам постит каждое одобренное объявление с фото,
  контактом владельца и CTA «разместить своё». Это витрина и источник новых подач.
- **Mini App** (aiohttp + один SPA) — веб-витрина с фильтрами; опубликована наружу через бесплатный
  Cloudflare-туннель (`deploy/apply_tunnel.sh`), постоянного домена пока нет — адрес меняется при
  рестарте туннеля.
- **Платный продукт один:** Boost = объявление поднимается на первое место в поиске и
  выделяется ⭐ на `BOOST_DAYS` дней. Три рельса оплаты (порядок кнопок = порядок удобства):
  **картой `CARD_PRICE_CENTS`** через Lemon Squeezy (мерчант-оф-рекорд, работает без юрлица и без
  карты, привязанной к Telegram), **`BOOST_MDL` MDL через MIA** — мгновенный перевод по номеру
  телефона из приложения любого банка Молдовы, с ручным подтверждением, **`BOOST_STARS` Telegram Stars ⭐**.

Метрика успеха (см. MISSION.md §18): **`REAL_REVENUE > 0`** — первый реальный платёж от клиента.
Считается по таблице `payments` и **не смешивается**: `revenue_card` (всё, что не `XTR`/`MDL`/`XTEST`,
то есть USD из Lemon Squeezy), `revenue_mdl` (`currency='MDL'`), `revenue_stars` (`currency='XTR'`);
тестовые платежи (`XTEST` и `is_test=1`) в выручку не попадают.

---

## 2. Карта репозитория

```
bot/                     код бота и Mini App (один venv, один репозиторий)
  main.py                точка входа: Bot/Dispatcher, подключение роутеров, запуск scheduler
  config.py              все настройки из ENV (единственный источник конфигурации)
  constants.py           подписи deal/ptype/status, список районов Кишинёва, ROOMS
  db.py                  схема SQLite + все SQL-запросы (единственный слой доступа к данным)
  keyboards.py           все inline-клавиатуры (единственное место, где живут callback_data)
  render.py              текстовые карточки объявлений (caption, кнопки списков)
  publish.py             выкладка одобренного объявления: скачивание фото + пост в канал
  lemon.py               клиент Lemon Squeezy: создание checkout-ссылки, проверка подписи X-Signature,
                        разбор order-вебхука в карточку заказа (единственный модуль, знающий LS)
  scheduler.py           фоновые задачи: применение карточных заказов, возвраты/чарджбеки, сторож
                        молчащего вебхука, напоминания о неподтверждённых переводах, Boost-нудж и
                        предложения купить снова тем, у кого Boost закончился
  requirements.txt       aiogram, aiosqlite, aiohttp, python-dotenv
  .env.example           шаблон ENV-файла (реальный .env в git не попадает)
  handlers/
    start.py             /start, /menu, справка, deep links (?start=метка, ?start=share_<id> —
                         приземление на расшаренное объявление, /startapp boost:<id>), /paysupport
    add.py               FSM подачи объявления (11 состояний) + уведомление админов
    browse.py            FSM поиска (deal → ptype → district → результаты с пагинацией)
    profile.py           «Мои объявления»
    boost.py             три платёжных рельса: карта (LS checkout), Stars-инвойс, ручной перевод MDL
    admin.py             модерация, подтверждение ручных платежей, /stats /pending /nudge /broadcast
    fallback.py          поддержка без фейков: любой plain-text, который не съели остальные роутеры
                         (нет команды, нет активной FSM-состояния), пересылается админам, а человеку —
                         «передал модератору». Подключается ПОСЛЕДНИМ в main.py, поэтому шаги
                         мастера подачи объявления не перехватываются; /paysupport обещает именно
                         этот канал, а не мёртвый @support
  webapp/
    app.py               aiohttp-сервер: /api/listings, /api/listing/{id}, POST .../contact, /api/stats,
                         /photos/ и POST /webhook/lemonsqueezy (входящий ящик карточных заказов)
    static/index.html    SPA-витрина (фильтры, карточка, «связаться», Boost для владельца)
deploy/                  всё, что связано с сервером и проверками (ничего не импортируется ботом)
  imobiliare.service     systemd unit бота
  imobiliare-web.service systemd unit Mini App
  nginx-chirie.conf      шаблон reverse-proxy (ждёт домен)
  apply_https.sh         домен → nginx + certbot → HTTPS для Mini App И вебхука LS (одной командой)
  apply_tunnel.sh        бесплатный HTTPS без домена: Cloudflare quick tunnel на 127.0.0.1:8085
                        (адрес случайный и меняется при рестарте — отсюда сторож вебхука)
  check_public.sh       «открыта ли фронтальная дверь»: три сервиса + /api/stats, витрина и
                        /webhook (ожидаем 401) по публичному адресу + очередь карточных заказов
  set_menu_button.py    кнопка Mini App в меню чата через Bot API setChatMenuButton (не @BotFather);
                        перезапускать после смены адреса туннеля
  apply_manual.sh       рельс MIA/MDL: ключи .env (PAY_PHONE/PAY_NAME), паритет цены Stars,
                        самотест текста инструкции (чистые функции keyboards) и рестарт
  apply_card.sh          / test_card_rail.py — приложить карточный рельс и проверить его без денег
  register_ls_webhook.py регистрация вебхука Lemon Squeezy через API LS (создаёт тестовый и боевой,
                        генерит секрет в .env, чистит вебхуки на умершем адресе, рестартует сервисы)
  apply_backup.sh        ставит ежедневный бэкап: /opt/imobiliare/backup.py + cron 03:00 + ротация 14
                        дней + снимок .env (0600) и фото; сам прогоняет первый снимок и проверяет
                        integrity_check, печатает команды восстановления
  run.ps1               Windows-помощник: scp файла на сервер и ssh-прогон с чистым UTF-8 выводом
                        (длинный inline-ssh в PowerShell 5.1 ломается, поэтому только файлами;
                        IP читается из ACCESS.md, сам файл ASCII; exit-код = код удалённой команды)
  harden_ssh.sh         fail2ban на sshd (бан 1 ч после 5 неудач, свой IP в ignoreip) + опциональный
                        `--disable-root-password` (root только по ключу), который отдельно требует
                        подтверждения из журнала sshd — это единственная команда проекта, способная
                        закрыть человеку доступ к его же серверу
  apply_nudge.sh, apply_manual.sh, verify_nudge.sh, verify_manual.sh, status.sh,
  cleanup_test.sh, clean_board.sh (снять ДЕМО-карточку и всё, что на неё ссылается, — чтобы наружу
  не было видно тестового), test_share.py (прогон реферальной петли «поделиться» на реальном .env,
  без денег и без записей → `✅ SHARE LOOP OK`), test_money_path.py, make_demo.py, post_promo.py, audit.py, whoami.sh,
  seed.py, mk_tmp_listing.py, setup_channel.py, test_render.py,
  test_publish.py (E2E «подача→модерация→канал→webapp», сам себя убирает → `✅ BOARD PIPELINE OK`),
  make_logo.py + logo_bot.png + logo_channel.png (бренд-семейство: дом с замочной скважиной, один знак
  на двух поверхностях — бот в бирюзе, канал в графите; перерисовать оба = `python deploy/make_logo.py`),
  set_logo.py `<bot.png> <channel.png>` (разные логотипы боту и каналу; setMyProfilePhoto понимает фото
  только через attach://; по умолчанию /tmp/logo_bot.png и /tmp/logo_channel.png), rename.py (единое видимое имя «Găsește Casa · Недвижимость Кишинёва» на боте и канале —
  idempotent, печатает `✅ RENAMED OK`; chiriebot остаётся только внутренним кодовым именем в логах),
  pin_roles.py (описание канала + закреплённый пост-навигация «бот=инструмент, канал=лента,
  доска=витрина»; one-shot, при перезапуске повторит пост),
  test_gift.py (E2E подарочных поднятий: грант → кнопка «🎁» → списание → топ,
  без единой строки в payments, сам себя убирает → `✅ GIFT LOOP OK`),
  test_all.py (E2E непокрытого: поиск-фильтры db+webapp API, поднятие-первым, рельсы оплаты Stars+MIA
  (is_test/XTEST — REAL_REVENUE не трогает) и возврат; baseline на входе=на выходе, сам себя убирает →
  `✅ ALL LOOP OK`),
  test_webapp.sh, apply_webapp.sh, fix_test.sh
MISSION.md / STATUS.md / GROWTH.md / RUNBOOK.md / TODO.md (бэклог: что реализуем потом) /
OUTREACH.md (боевой комплект аутрича: офер 🎁, тексты, контакты, трекер)
.gitattributes           LF для .sh/.py/.service/.html (иначе Windows CRLF ломает bash на сервере) и
                        binary для .db/.png — `sed -i 's/\r$//'` остаётся страховкой, а не необходимостью
ACCESS.md                (в .gitignore) SSH, пароли, IP, id бота/канала
```

Стек: **Python 3.12, aiogram 3 (polling, не webhook), aiosqlite, aiohttp, SQLite (WAL), systemd, Ubuntu 24.04.**
Никаких фреймворков, ORM, Docker, celery, redis — осознанно (см. §14 «Решения»).

---

## 3. Архитектура и потоки данных

```
Telegram-пользователь ──> aiogram polling ──> handlers/* ──> db.py (aiosqlite, WAL) ──> /opt/imobiliare/data/imobiliare.db
                                  │                                   ▲
                                  ├──> publish.py ──> канал @gaseste_casa (posts + фото)
                                  │                                   │
scheduler.py (тот же процесс) ────┘  нудж + apply_card_orders + сторож ┘

webapp/app.py (aiohttp, 127.0.0.1:8085)  ← читает ту же БД, пишет views/contacts, хранит card_orders
   ▲  ▲ SPA index.html (витрина)
   └── публичный HTTPS: cloudflared quick tunnel (apply_tunnel.sh)
                       или nginx + certbot (apply_https.sh)
Lemon Squeezy ──POST /webhook/lemonsqueezy (подпись X-Signature)──> тот же туннель → card_orders
```

Разделение ответственности в деньгах: **веб-процесс только принимает** карточные заказы (подпись →
таблица `card_orders`), **бот-процесс единственный, кто начисляет** Boost и пишет `payments`.
Так вебхук не может ни удвоить платёж, ни остаться с начислением без записи — всё решает флаг
`card_orders.applied`.

Три важных факта:

1. **Одна SQLite на два процесса.** Поэтому `PRAGMA journal_mode=WAL` и `busy_timeout=5000`
   обязательны — без них бывают `database is locked`.
2. **Бот на polling, не на webhook.** Наружу не смотрит ничего, кроме публичного HTTPS: постоянный
   путь — nginx + certbot (`apply_https.sh`, `ufw allow 'Nginx Full'`), временный — cloudflared
   quick tunnel (`apply_tunnel.sh`), у которого **нет входящих портов вообще** (туннель сам ходит к
   Cloudflare), поэтому `ufw` может держать открытым только SSH :22. Вебхук Lemon Squeezy принимает
   **webapp** (`/webhook/lemonsqueezy`), не бот — то есть деньги приходят в процесс, который и так слушает
   127.0.0.1:8085, а бот их только начисляет.
3. **Порядок роутеров в `main.py` имеет значение:** `start, add, browse, profile, boost, admin`.
   `boost.router` должен быть раньше `admin.router`, потому что админ обрабатывает `manapp:`/`manrej:`,
   а буст — `manual:`/`manualpaid:` (префикс `man` общий). Обработчики выбраны так, чтобы не коллизировать.

---

## 4. Конфигурация (ENV)

Единственный источник — `/opt/imobiliare/bot/.env` (права root:600), читается `python-dotenv`.
Ключей и значений по умолчанию:

| Ключ | По умолчанию | Зачем | Текущее прод-значение |
|---|---|---|---|
| `BOT_TOKEN` | пусто (бот не стартует) | токен от @BotFather | секрет → `ACCESS.md` |
| `ADMIN_IDS` | пусто | через запятую tg id; доступ к модерации и `/stats` | `1182157446` |
| `DB_PATH` | `/opt/imobiliare/data/imobiliare.db` | файл SQLite | по умолчанию |
| `PHOTOS_DIR` | `/opt/imobiliare/data/photos` | jpg для Mini App, имена `{listing_id}_{i}.jpg` | по умолчанию |
| `DEFAULT_CURRENCY` | `EUR` | валюта prices в боте | `EUR` |
| `BOOST_STARS` | `400` | цена Boost в Telegram Stars (× $0.013 вознаграждения ≈ $5.20) | `400` |
| `BOOST_DAYS` | `7` | на сколько дней Boost | `7` |
| `BOOST_MDL` | `0` (опция скрыта) | цена Boost в MDL переводом; **0 выключает рельс** | `100` |
| `PAY_PHONE` | пусто | **номер телефона получателя для MIA**; задан → рельс выглядит как мгновенный перевод по телефону | **пусто — надо заполнить** |
| `PAY_NAME` | пусто | имя получателя, которое банк покажет на экране подтверждения (знак «платишь тому, кому надо») | **пусто — надо заполнить** |
| `PAY_INFO` | пусто | дополнительные реквизиты одной строкой (карта/IBAN), если принимаете и так | **пусто — надо заполнить** |
| `LS_API_KEY` | пусто | ключ API Lemon Squeezy (Settings → API) | **пусто — нет аккаунта** |
| `LS_STORE_ID` | пусто | id магазина (число в URL страницы товара) | пусто |
| `LS_VARIANT_ID` | пусто | id варианта товара (Products → ваш товар → Variant ID) | пусто |
| `LS_WEBHOOK_SECRET` | пусто | signing secret из Settings → Webhooks; проверяется подпись `X-Signature` | пусто |
| `CARD_PRICE_CENTS` | `500` | цена Boost в карточке LS, **в US центах** ($5.00 ≈ 90 MDL) | `500` |
| → `CARD_ENABLED` | *(вычисляется)* | `True` только если заданы API_KEY + STORE_ID + VARIANT_ID; включает кнопку «💳 Картой» | **False** |
| `NUDGE_AFTER_DAYS` | `2` | через сколько дней после подачи напоминать про Boost | `2` |
| `NUDGE_COOLDOWN_DAYS` | `4` | как часто можно напоминать по одному объявлению | `4` |
| `NUDGE_INTERVAL_HOURS` | `6` | как часто напоминать про Boost (цикл спит **первым**, значит первый нудж не раньше 6 ч после рестарта; карточный ящик при этом опрашивается каждые 60 с) | `6` |
| `GIFT_BOOSTS` | `1` | сколько бесплатных поднятий получает каждый из первых `GIFT_SLOTS` (0 = программа выключена) | `1` |
| `GIFT_SLOTS` | `10` | сколько человек получают подарок (scarcity: "осталось N мест") | `10` |
| `CONTACT_URL` | пусто | контакт поддержки в `/paysupport` | пусто |
| `CHANNEL_ID` | пусто → публикация в канал выключена | `@username` публичного канала или `-100...` id | `@gaseste_casa` |
| `CHANNEL_URL` | *(вычисляется)* | `https://t.me/<username>` если `CHANNEL_ID` начинается с `@` | t.me/gaseste_casa |
| `BOT_USERNAME` | пусто | для CTA-кнопок в постах канала | `gaseste_casa_bot` |
| `BOT_URL` | *(вычисляется)* | `https://t.me/<BOT_USERNAME>` | — |
| `WEBAPP_URL` | пусто → кнопка «Открыть приложение» скрыта | публичный HTTPS адрес Mini App; **он же базовый URL для вебхука Lemon Squeezy** | **пусто (нет домена)** |
| `STARS_CURRENCY` | `XTR` (константа, не ENV) | валюта Stars-инвойсов | `XTR` |

Правило: **любая новая настройка = ключ в `config.py` + строка в `.env.example` + строка в эту таблицу.**
Никаких значений, зашитых в обработчиках.

---

## 5. База данных

Схема создаётся автоматически при старте бота (`db.init_db()` → `executescript(SCHEMA)`),
дополнительные колонки — миграцией `_migrate()` в том же месте. Mini App вызывает то же самое через
`db.ensure_schema(conn)`, поэтому порядок старта сервисов не важен, а первичного создания БД руками не нужно.

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE NOT NULL, username TEXT, first_name TEXT, phone TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    deal TEXT NOT NULL,            -- rent | sale
    ptype TEXT NOT NULL,           -- apartment | house | room
    rooms INTEGER, price INTEGER NOT NULL, currency TEXT DEFAULT 'EUR',
    district TEXT, area REAL, floor INTEGER, floors INTEGER,
    phone TEXT, description TEXT,
    photos TEXT DEFAULT '[]',      -- JSON-массив Telegram file_id (не пути!)
    status TEXT DEFAULT 'pending', -- pending | active | rejected | archived
    boosted_until TEXT,            -- datetime('now','+N days') — признак живого Boost
    nudged_at TEXT,                -- добавлено миграцией: когда последний раз напоминали про Boost
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
    views INTEGER DEFAULT 0
);
CREATE TABLE boosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    stars INTEGER NOT NULL,        -- цена в Stars; для ручных заявок туда пишется сумма в MDL
    days INTEGER NOT NULL,
    status TEXT DEFAULT 'pending', -- pending | paid | manual_pending | manual | manual_rejected | refunded
    order_ref TEXT,                -- миграция: что оплатило этот Boost — 'ls:<order_id>' | 'tg:<charge_id>' | 'manual:<boost_id>'
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, listing_id INTEGER,
    purpose TEXT,                  -- boost | boost_test | boost_manual | boost_card | boost_card_test
    stars INTEGER,                 -- сумма в Stars (0 для MDL и карты)
    currency TEXT,                 -- XTR | XTEST | MDL | USD (валюта карточного мерчанта)
    telegram_charge_id TEXT, provider_charge_id TEXT,  -- для карты = LS order identifier (UUID)
    amount REAL,                   -- миграция: реальная сумма в валюте платежа (MDL или USD)
    is_test INTEGER DEFAULT 0,     -- миграция: платёж в тестовом режиме провайдера
    refunded_at TEXT,              -- миграция: деньги вернули → строка остаётся, в выручку не считается
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE card_orders (         -- ВХОДЯЩИЙ ЯЩИК карточных платежей (пишет webapp, читает бот)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL, -- LS attributes.identifier; UNIQUE = защита от дублей вебхука
    listing_id INTEGER, tg_id INTEGER,   -- прилетают из meta.custom_data checkout'а
    amount_cents INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
    test_mode INTEGER NOT NULL DEFAULT 0, customer TEXT,
    applied INTEGER NOT NULL DEFAULT 0,  -- 1 = Boost уже начислен, повторно не начислить
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, user_id INTEGER, listing_id INTEGER, meta TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_listings_status ON listings(status);
CREATE INDEX idx_listings_district ON listings(district);
```

Ключевые запросы (все живут в `db.py`, менять только там):

- выдача поиска: `WHERE status='active'` +
  `ORDER BY (boosted_until IS NOT NULL AND boosted_until > datetime('now')) DESC, created_at DESC` —
  то есть Boost — это **не отдельная таблица, а колонка-просрочка**;
- `listings_to_nudge(after_days, cooldown_days, limit=20)` — активные, не в бусте, старше N дней,
  не nudged recently;
- `store_card_order(conn, order)` — вызывается **webapp-ом** на его соединении; вставляет заказ,
  при `IntegrityError` (повторная доставка того же `order_id`) только обновляет `status` и возвращает
  `False` ⇒ удвоение платежа невозможно на уровне схемы;
- `card_orders_to_apply(limit=20)` → `applied=0 AND status='paid' AND listing_id IS NOT NULL`;
  `mark_card_order_applied(id)` — ставит флаг после начисления;
- три запроса для сторожа вебхука: `card_checkout_ago_seconds()` (секунды с последней выданной
  ссылки оплаты, `None` если никогда не выдавалась), `card_checkouts_since(minutes)` (сколько ссылок
  выдали в окне) и `card_orders_since(minutes)` (сколько доставок реально пришло в окне);
- `get_user_by_tg_id(tg_id)` — читает покупателя, **не создавая** его (в отличие от
  `get_or_create_user`, который затёр бы NULL-ами username/first_name);
- возвраты: `payments_refunds()` (оплаченные и уже применённые заказы, у которых `card_orders.status`
  стал `refunded`/`fraudulent`, а `payments.refunded_at` ещё пуст), `mark_payment_refunded(id)`,
  `find_boost_by_ref('ls:<order_id>')`, `latest_boost_for_listing(id)`, `unboost_listing(id)`;
  `find_payment_by_charge('manual:<boost_id>')` — та же честность для возврата MIA-перевода, где
  `card_orders` нет и платёж ищется по `payments.telegram_charge_id`;
  `partial_refund` в выборку **не входит** — частично возвращенные деньги остаются выручкой и Boost
  остаётся (статусы заказа LS проверены по API-докам: `pending | failed | paid | refunded |
  partial_refund | fraudulent`);
- напоминания: `manual_claims_waiting(older_than_hours)` (неподтверждённые MIA-заявки вместе с
  телефоном плательщика с объявления и `waited_hours`), `boosts_that_just_expired(hours)` (объявления,
  у которых Boost закончился, и про которые ещё не писали), `event_recent(name, meta, hours)` —
  дедупликация напоминаний через `events`, поэтому она переживает рестарт;
- `stats()` — пользователи, объявления, `boosts WHERE status IN ('paid','manual')`,
  `revenue_stars` (`XTR`, `is_test=0`), `revenue_mdl` (`amount`, `MDL`, `is_test=0`),
  `revenue_card` (`amount`, валюта вне `XTR/MDL/XTEST`, `is_test=0`) — **все три линии дополнительно
  с `refunded_at IS NULL`**, то есть возвращенные деньги в REAL_REVENUE не попадают;
  `card_unapplied` (не начисленные оплаченные карточные заказы = **индикатор здоровья вебхука**),
  `refunds` (сколько денег вернули), `manual_waiting` (сколько переводов ждут ручного подтверждения),
  `test_payments` (`XTEST` или `is_test=1`).

Полезные ручные проверки (на сервере, `sqlite3` CLI не ставился — через venv-питон):

```bash
/opt/imobiliare/venv/bin/python -c "import sqlite3;d=sqlite3.connect('/opt/imobiliare/data/imobiliare.db');\
print(d.execute('select count(*) from listings').fetchone(), d.execute(\"select status,count(*) from listings group by status\").fetchall(),\
d.execute('select sum(amount) from payments where currency=\"MDL\"').fetchone())"
```

---

## 6. Пространство имён callback_data и FSM

Полная таблица — чтобы не придумывать конфликтов при новых кнопках.

| Префикс | Обработчик | Несёт | Что делает |
|---|---|---|---|
| `menu:home/search/add/my/help` | start.py, browse.py, add.py, profile.py | — | главное меню и входы в сценарии |
| `cancel` | везде (fallback-кнопки) | — | отмена FSM |
| `deal:` `ptype:` `rooms:` `district:` `add:next` `skip:next` `add:back` `add:photos_done` `add:photos_skip` `add:confirm` | add.py | значение | шаги подачи: панель 1/5 (сделка+тип+комнаты), район, пропуск деталей, шаг назад |
| `sf:deal:` `sf:ptype:` `sf:rooms:` `sf:pick:district` `sf:pick:price` `sf:dist:` `sf:price:` `sf:panel` `sf:go` `sf:reset` `page:next` `page:prev` `noop` | browse.py | значение/`all` | единая панель фильтров, пикеры района/цены, пагинация (всё edit-in-place) |
| `view:` | browse.py | listing_id | карточка объявления (+inc_views, если не владелец) |
| `contact:` | browse.py | listing_id | показать телефон/юзернейм владельца |
| `boost:` | boost.py | listing_id | оффер Boost (все включённые рельсы); при `manual_pending` — вместо оффера «⏳ платёж на проверке» |
| `gift:` | boost.py | listing_id | списать подарочное поднятие (`users.boost_credits`, статус `gift`, денег не касается) |
| `card:` | boost.py | listing_id | создать LS checkout и дать URL-кнопку «🔗 Открыть оплату картой» |
| `boostpay:` | boost.py | listing_id | `send_invoice` в XTR |
| `manual:` | boost.py | listing_id | `keyboards.manual_pitch`: шаги MIA/перевода + копируемый блок |
| `manualpaid:` | boost.py | listing_id | заявка «я перевёл» → `boosts.status='manual_pending'` + уведомления админам |
| `mod:approve:` `mod:reject:` | admin.py | listing_id | модерация |
| `manapp:` `manrej:` | admin.py | boost_id | подтвердить / отклонить ручной платёж |
| `manref:` `manrefyes:` | admin.py | boost_id | подтвердить возврат MIA-перевода (два тапа: спросить → снять деньги с выручки и с топа) |
| `back:search` | browse.py | — | вернуться к результатам (или к панели фильтров, если фильтров не было) |

Состояния FSM: `AddListing = specs, district, price, area, floor, description, phone, photos, confirm`
(9 состояний; подписи «Шаг 1/5…», четыре необязательных экрана деталей делят один шаг 4/5;
«⬅️ Назад» идёт по `STEP_ORDER` и сохраняет уже введённое). `Search = on` — одно состояние:
панель, пикеры и результаты редактируют одно и то же сообщение. Хранилище состояний — MemoryStorage
(не сохраняется между рестартами: недоподанное объявление просто теряется, это осознанно принято).

---

## 7. Пользовательские сценарии (как выглядит изнутри)

**Подача объявления:** `/start` → «➕ Разместить объект» → «Шаг 1/5» — одна панель: сделка + тип +
комнаты (чипы с ✓, «Далее» проверяет, что всё выбрано) → «Шаг 2/5» район из `constants.DISTRICTS` →
«Шаг 3/5» цена числом → необязательные детали (площадь/этаж/описание/телефон — «Пропустить ⏭»,
«⬅️ Назад» возвращает на шаг назад и сохраняет всё введённое) → «Шаг 5/5» 1–10 фото
(берётся `message.photo[-1].file_id`) → сводка «✅ Опубликовать» → `db.create_listing(status='pending')`
→ `events.listing_created` → всем админам карточка с кнопками «Одобрить/Отклонить».
Кнопочные переходы редактируют одно сообщение (`_goto`), чат не засоряется.

**Модерация:** `mod:approve` → `status='active'` → владельцу «одобрено» + **`publish.publish_listing`**
(скачать фото в `PHOTOS_DIR` как `{id}_{i}.jpg`, затем в канал: `send_media_group` с caption
+ отдельное сообщение с кнопками «Написать владельцу / Смотреть все объекты / ➕ Разместить своё»),
`events.channel_post` c id сообщений в `meta`. Публикация обёрнута в `try/except` — сбой канала
**никогда не должен** блокировать модерацию. `mod:reject` → статус + уведомление владельцу.

**Поиск:** «🔍 Поиск» → сделка → тип → район → список кнопками (текст из `render.listing_button_text`,
⭐ в начале если бустнутый) по 10 на страницу → карточка.

**Boost из бота:** карточка своего объявления → «🚀 Поднять (Boost)» → оффер со всеми включёнными
рельсами (карта → MDL → Stars). Чужое объявление — «Поднять можно только своё». Если заявка
«я перевёл» уже висит (`manual_pending`) — вместо оффера «⏳ платёж на проверке» (защита от двойного
перевода); если Boost уже активен — в оффере строка «⚡ активен до <дата> — новый продлится».
Карточный путь: «💳 Картой $5.00» → бот создаёт LS checkout → URL-кнопка на платёжную страницу →
покупатель платит → LS стучится вебхуком → бот сам начисляет Boost и пишет покупателю и админам.

**Deep links:**
- `t.me/gaseste_casa_bot?start=<метка>` — метка пишется в `events.start.meta` (реестр источников трафика),
  используется во всех рассылках/постах;
- `/startapp boost:<id>` — из Mini App (SPA зовёт `Telegram.WebApp.openTelegramLink`), сразу показывает оффер.

**Mini App API** (`webapp/app.py`, порт 127.0.0.1:8085):

| Метод/путь | Авторизация | Что делает |
|---|---|---|
| `GET /` | — | отдаёт `static/index.html` |
| `GET /api/listings?deal=&ptype=&district=&rooms=&price_max=&page=` | — | активные, буст сначала, 20/страница |
| `GET /api/listing/{id}` | initData в заголовке `X-Init-Data` (опционально) | детали; `views+1`; `events.web_view` |
| `POST /api/listing/{id}/contact` | **обязательна** initData | телефон/ tg владельца; `events.web_contact` (401 без авторизации) |
| `GET /api/stats` | — | `{"active": N}` |
| `POST /webhook/lemonsqueezy` | **подпись `X-Signature`** (не initData) | ящик карточных заказов: 401 bad signature, 400 bad json, 200 `{"ok":true,"ignored":true}` для не-order событий, иначе `{"ok":true,"recorded":true/false}` |
| `GET /photos/{file}` | — | статика из `PHOTOS_DIR` |

Валидация initData — детерминированная по документации Telegram: `secret = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)`,
`hash = HMAC_SHA256(key=secret, msg=отсортированные "k=v\n" без hash)`, `auth_date` не старше 24 ч.
Сломанная подпись → `None` (для `/contact` это 401).

---

## 8. Деньги: три рельса + чем они ломаются

Порядок кнопок в `keyboards.boost_kb` = порядок удобства клиента: **карта → MDL → Stars**.
Текст цен везде берётся из одного места — `keyboards.rails_line()` / `boost_pitch()` (справка бота,
оффер, нудж, демо-скрипт), поэтому «переписать цену» = одна правка.

### Рельс 1 — карта (Lemon Squeezy, merchant of record)

Самый «человеческий» путь: клиент платит Visa/Mastercard на защищённой странице банка, деньги идут к LS,
LS переводит их нам на банк, а мы только получаем уведомление. Никакого эквайринг-договора,
никакого юрлица на старте, карта не привязана к Telegram.

**Почему Lemon Squeezy, а не Stripe/maib/bpay (проверено 10.10 по официальным спискам):** Stripe
не выдаёт мерчант-аккаунтов в Молдове; местный эквайринг (maib, bpay, Fastpay) требует ÎI/SRL и
договора; LS **включает Молдову в список стран для банковских выплат** мерчанту и сам закрывает
VAT/чарджбеки как merchant of record.

```
card:<id> (boost.card_offer) → проверка ownership → events.card_click
  → lemon.create_checkout(listing_id, tg_id)  # POST /v1/checkouts
       custom_price = CARD_PRICE_CENTS, enabled_variants = [LS_VARIANT_ID],
       checkout_data.custom = {listing_id, tg_id}, expires_at = +3 ч
  → events.card_checkout (meta = id checkout'а) → URL-кнопка "🔗 Открыть оплату картой"
КЛИЕНТ ПЛАТИТ → LS шлёт POST /webhook/lemonsqueezy (webapp-процесс):
  verify_signature(raw_body, X-Signature) → 401 если не совпало
  parse_order_event → card_orders (store_card_order, UNIQUE order_id) → 200
scheduler.apply_card_orders (бот-процесс, каждые 60 с, ЕДИНСТВЕННЫЙ писатель денег):
  card_orders_to_apply (applied=0 AND status='paid')
  → boost_listing(BOOST_DAYS) + boosts(status='paid', stars=0, order_ref='ls:<order_id>')
  → payments(purpose='boost_card', currency=USD, amount=total/100,
              provider_charge_id=order_id, is_test=test_mode)
  → mark_card_order_applied + events.boost_card_paid + ЛС покупателю + ЛС каждому админу
scheduler.apply_refunds (тот же тик, те же 60 с):
  payments_refunds (applied=1, card_orders.status IN ('refunded','fraudulent'), refunded_at IS NULL)
  → payments.refunded_at=now → boosts.status='refunded'
  → unboost_listing, только если этот boost последний для объявления (более поздняя оплата ≠ снимаем)
  → events.boost_refunded + ЛС владельцу + ЛС админам
```

**Железные требования провайдера (иначе касса не работает):**
- **публичный HTTPS-хост обязателен.** `GET /v1/orders` не возвращает `custom_data`, то есть
  опрашивать LS список заказов бесполезно — атрибуция «какой это объект» живёт только в вебхуке.
  Постоянный вариант — `deploy/apply_https.sh <домен>` (nginx + certbot), временный бесплатный —
  `deploy/apply_tunnel.sh` (Cloudflare quick tunnel, адрес `https://<случайное>.trycloudflare.com`).
  У туннеля две цены: адрес **меняется при каждом рестарте** (его надо пересохранять в LS) и он
  живёт, пока жив unit `cloudflared-tunnel`. Именно поэтому рядом стоит сторож `check_webhook_silence`
  (§9): он замечает «ссылку дали, денег не дополучили» за 15 минут, а не в конце месяца.
- подпись — заголовок **`X-Signature`** = HMAC-SHA256 **raw body** (hex) на `LS_WEBHOOK_SECRET`.
  Хедер называется не `X-LemonSqueezy-Signature`; читать надо `await request.read()`, а не
  уже распарсенный JSON (иначе канонизация тела сломает подпись).
- отвечать **200** всегда, кроме случая плохой подписи: иначе LS ретраит доставку 3 раза.
- `custom_data` лежит в **верхнеуровневом `meta`** payload'а, а не в `data.attributes`.
- статусы заказа: `pending | failed | paid | refunded | partial_refund | fraudulent`; начисляем
  только `paid`. `test_mode=True` → начисляем Boost, но в `payments.is_test=1` и `purpose='boost_card_test'`,
  то есть в `REAL_REVENUE` такой платёж не попадает.

**Возврат и чарджбек (закрыто 11.10).** `order_refunded` прилетает тем же путём: `store_card_order`
видит повторный `order_id` и только обновляет `status`, дальше `apply_refunds` помечает платёж
`refunded_at`, ставит `boosts.status='refunded'` и снимает объявление с топа, если этот Boost был
последним. Повторная доставка того же возврата ничего не делает (`refunded_at IS NULL` в выборке),
`partial_refund` не трогает ничего — деньги, которые остались, настоящие. Возврат вычитается из всех
трёх линий `REAL_REVENUE`. **Оставшийся долг:** у MIA нет API статуса/отзыва перевода (схема только
push-платежи, проверяется выпиской в банке), поэтому возврат по MIA остаётся ручным: `boosted_until`
снимает админ, а строки от MIA в `card_orders` нет вообще — автоматике там ловить нечего.

### Рельс 2 — MIA / перевод MDL с ручным подтверждением

Работает без эквайринга, без карты в Telegram, без ÎI на старте — **страховка, если LS-аккаунта нет**,
и при этом самый «человеческий» путь для Молдовы. Кнопка «💰 MIA 100 MDL» (или «💰 Перевод 100 MDL»,
если номер не задан).

**Что такое MIA (проверено 11.10 на mia.bnm.md — сайт БНМ):** национальная схема мгновенных платежей.
Физлицо открывает своё банковское приложение → «MIA · Мгновенные платежи» → «Перевод P2P» → вводит
**номер телефона** получателя → видит имя получателя и подтверждает. Никаких реквизитов, номера карты,
3-D Secure и кассового сбора для переводящего; зачисление секунды. Активировать MIA надо один раз —
в своём банке, и это же надо сделать владельцу проекта, чтобы **принимать** переводы.

```
boost_kb → manual:<id> → keyboards.manual_pitch(id): шаги MIA + тап-копируемый блок
                                   <code>телефон / имя / PAY_INFO / Boost #id</code>
        → manualpaid:<id> → boosts(status='manual_pending', stars=BOOST_MDL, days=BOOST_DAYS)
        → каждому админу: сумма, район и цена объявления, **номер телефона плательщика**
          (row.phone с объявления — именно его банк покажет в выписке), комментарий-референс
          «Boost #id», boost_id + кнопки manapp:<boost_id> / manrej:<boost_id>
        → manapp → boosts.status='manual', boosts.order_ref='manual:<boost_id>', boost_listing(days),
                   payments(purpose='boost_manual', currency='MDL', amount=BOOST_MDL,
                            telegram_charge_id='manual:<boost_id>'),
                   events.boost_manual_paid, владельцу «платёж получен, топ на N дней»,
                   админу тем же сообщением кнопка manref:<boost_id> («деньги вернули — снять Boost»)
        → manrej  → boosts.status='manual_rejected', владельцу «не нашли деньги, попробуй ещё раз»
        → manref  → админу подтверждение (MDL, объявление, «нажимай Да, только если деньги уже ушли»)
        → manrefyes → scheduler.refund_manual_claim(): payments.refunded_at, boosts.status='refunded',
                      unboost (только если этот Boost последний), events.manual_refunded,
                      владельцу «деньги возвращены», остальным админам «оформил: @username»
scheduler.check_manual_claims (бот-процесс, каждые 60 с):
  manual_pending старше MANUAL_REMIND_HOURS → то же сообщение админам ещё раз,
  не чаще одного раза на окно (дедуп = events.name='manual_remind', meta=boost_id)
```

**Возврат перевода (закрыто 11.10).** У MIA нет API отзыва: вернуть деньги можно только своим
переводом в банковском приложении. Чтобы эта ручная операция оставляла корректную бухгалтерию, `admin.py`
предлагает админу вторую кнопку (`manref` → `manrefyes`), а `scheduler.refund_manual_claim()` делает ровно
те же три записи, что и карточный возврат: MDL перестают считаться выручкой (`payments.refunded_at`),
Boost гаснет, топ освобождается, если позже клиент ничего не оплатил. Функция идемпотентна (повторный тап
отвечает «уже отмечен») и отказывается работать по неоплаченной заявке. Проверено тестом
(`test_card_rail.py` → блок `MIA refund`).

Весь текст платёжной инструкции собирается в одном месте — `keyboards.manual_pitch()` (плюс
`pay_lines()` / `pay_block()` / `manual_reference()`), поэтому он проверяется как чистая функция
и правка формулировки не требует трогать обработчики. `PAY_INFO` и `PAY_NAME` экранируются через
`html.escape`: реквизиты с `<` или `&` раньше ломали бы отправку сообщения.

Защита от дублей: `find_boost(listing_id, 'manual_pending')` — вторая заявка по тому же объявлению
не создаёт записи, покупатель видит «Твой платёж уже на проверке».
Хранение цены: для ручных заявок `boosts.stars` = договорная сумма в **MDL** (комментарий в коде есть);
в `payments` это разведено: `stars=0, amount=<MDL>`.
Если `PAY_PHONE` и `PAY_INFO` пустые, покупатель видит «реквизиты пришлёт модератор» — это **не**
рабочий путь, держим заполненным.

### Рельс 3 — Telegram Stars

`boostpay:<id>` → `send_invoice(currency='XTR', provider_token='', prices=[LabeledPrice('Boost', BOOST_STARS)],
payload='boost:<id>')` → `pre_checkout_query` (всегда `ok=True`) → `message.successful_payment` →
`boost_listing` + `boosts(status='paid', order_ref='tg:<telegram_payment_charge_id>')` +
`payments(purpose='boost'|'boost_test', currency=sp.currency, stars=total_amount, telegram_charge_id=…)` +
`events.boost_paid` (или `test_paid`) + отчёт админам.

**Сколько это денег (проверено 11.10 по developer terms Telegram, §6.2.4):** вознаграждение мерчанту
= **$0.013 за звезду**, то есть `BOOST_STARS=400` ≈ $5.20 — ровно тот же порядок, что карточный рельс.
Старое значение 50 (≈ $0.65) было дырой: клиент всегда мог выбрать самый дешёвый рельс. Цена
по-прежнему задаётся одним ключом `.env`, решение человека — менять её или выключить рельс.
Нюансы вывода: полученные звёзды становятся доступны для вознаграждения до **21 дня**, вывод идёт
через Fragment в GRAM/TON (нужен криптокошелёк), минимальная сумма вывода — ограничение Fragment.
Это «карточный» путь для клиента (Telegram сам списывает карту), но не банковский перевод нам.

### Почему выбраны эти способы оплаты (проверено 11.10, источники — официальные документы)

| Вариант | Вердикт | Почему |
|---|---|---|
| **MIA (НБМ)** | ✅ основной путь первой недели | нет провайдера, нет KYC, нет договора, нет комиссии; клиенту достаточно номера телефона |
| **Lemon Squeezy** | ✅ карточный рельс, включается после аккаунта+активации | MoR, Молдова в списке стран выплат, карта+Apple/Google Pay на чужой странице; риск — категория товара (§10) |
| **Telegram Stars** | ✅ как удобство, не как «наш вывод» | оплата своей картой в интерфейсе Telegram, но кэш-аут через Fragment и до 21 дня ожидания |
| Stripe | ❌ | не выдаёт мерчант-аккаунтов в Молдове |
| **Polar.sh** | ❌ отвергнут после проверки AUP | их Acceptable Use Policy (ред. 25.03.2026) запрещает «marketplaces» и «продукты, которые позволяют другим продавцам продавать», а «directories and boards» — отдельная запретная-с-оговоркой категория; плюс review до 14 дней |
| PayPal | ❌ | в Молдове PayPal работает только «отправлять»: принимать и выводить на местный банк нельзя |
| Gumroad / Paddle / Whop | ❌ на нашем масштабе | тот же MoR-риск по категории + страны выплат/онбординг, ничего из этого не быстрее LS |
| местный эквайринг (maib Mastercard Gateway, bpay, Fastpay, MoldCard) | 🔜 когда пойдёт объём | принимает карты молдавских банков, включая Moldcard, в MDL без конверсии и с привычным 3DS, но требует ÎI/SRL и договора |
| крипта (USDT/TON) | ❌ как основной | клиент ради 100 MDL не купит USDT; годится только как запасной канал для редких покупателей |

**Практический вывод:** до первой транзакции доводим MIA (2 минуты на человека: телефон+имя в `.env`)
и параллельно активим LS. Локальный эквайринг — это следующий месяц, когда будет ÎI и объём: он
единственный закрывает «у меня карта молдавского банка, я плачу в интернете» без компромиссов.

### Что известно про ошибки Telegram (проверено на проде 10.10)

| Сообщение | Когда | Что значит |
|---|---|---|
| `PROVIDER_ACCOUNT_INVALID` | пользователь пытается **купить** звёзды | к аккаунту Telegram не привязана карта. Не баг бота |
| «этот метод оплаты невалиден для этого типа товара» | на экране оплаты Stars-инвойса при 0 звёзд | Telegram пытается докупить звёзды и отказывает. `send_invoice` при этом успешен (в логах чисто) — вина не кода |
| `PAYMENT_PROVIDER_INVALID` | бот шлёт тестовый XTEST-инвойс | продакшн-ботам тестовые платежи **запрещены**; XTEST работает только ботам в test-окружении. Обработчик `XTEST` в коде оставлен на будущее |

**Вывод, который надо помнить:** проверить кассу «бесплатно» нельзя ни одним способом.
Бесплатных прогонов два: **рельс MDL** руками (см. §11 «тест без денег») и **`deploy/test_card_rail.py`**
— он подделывает вебхук Lemon Squeezy (с честной HMAC-подписью на известном секрете) и прогоняет
весь путь кассы, ничего не оплачивая. Остальное — только реальные деньги.

### Что известно про Lemon Squeezy (проверено 10.10 по официальной документации)

| Факт | Последствие для нас |
|---|---|
| Молдова **есть** в списке стран для банковских выплат мерчанту | вывод денег на счёт возможен без ÎI на старте |
| Stripe не выдаёт мерчант-аккаунт в MD, местный эквайринг требует юрлицо | LS — единственный «как у людей» путь прямо сейчас |
| `POST /v1/checkouts` принимает `custom_price` и `checkout_data.custom` | товар в магазине нужен **один**, цена и объект — на лету |
| `GET /v1/orders` не отдаёт `custom_data` | опрос списка заказов бесполезен ⇒ вебхук и публичный HTTPS обязательны |
| вебхук-подпись = `X-Signature`, HMAC-SHA256 hex от **сырого тела** | сверять до `json.loads`, иначе подпись не сходится |
| `custom_data` в верхнеуровневом `meta` | читать `event["meta"]["custom_data"]`, а не `data.attributes` |
| нераспознанные/не-order события надо закрыть 200 | иначе LS ретраит доставку и засоряет логи |

---

## 9. Двигатель выручки (`scheduler.py`)

В том же процессе, что и бот: `asyncio.create_task(scheduler.run(bot))` рядом с `dp.start_polling`,
`job.cancel()` в `finally`. Один цикл, два ритма: **каждые `CARD_TICK_SECONDS=60` с** —
`apply_card_orders` (начисление карточных платежей), `apply_refunds` (возвраты),
`check_webhook_silence` (сторож) и `check_manual_claims` (напоминания о неподтверждённых переводах);
накопилось `NUDGE_INTERVAL_HOURS*3600` с — `nudge_unboosted` (предложить Boost тем, кто не в топе)
и `notify_expired_boosts` (предложить купить снова тем, у кого Boost закончился). Цикл **спит первым**,
поэтому после рестарта ничего не наваливается сразу. Любое исключение внутри прохода ловится и
**не убивает** цикл
(`except asyncio.CancelledError: raise` выше общего `except` — иначе задача не осталась бы отменяемой).
Каждая задача пишет в лог только когда реально что-то сделала.

`nudge_unboosted(bot)`: для каждой строки из `listings_to_nudge` → владельцу ЛС с текстом
(объявление, район, цена, просмотры) и `boost_kb` (все включённые рельсы, текст из `boost_pitch()`);
`mark_nudged(listing_id)`; `events.boost_nudge`. Если ЛС недоступны (пользователь заблокировал бота) —
запись всё равно помечается, чтобы не долбить каждый проход. Вручную тот же проход: `/nudge`.

`apply_card_orders(bot)`: читает `card_orders` (`applied=0 AND status='paid'`), для каждого —
`boost_listing` + `boosts(status='paid', order_ref='ls:<order_id>')` + `payments` + флаг `applied=1`
+ уведомления покупателю и админам. Если объявление или покупатель не найдены — строка остаётся
**неприменённой**, а в `events` летит `card_order_unmatched`: деньги не теряются, а `/stats` покажет
их в `card_unapplied` как сигнал «разбирайся руками». Повторная доставка вебхука ничего не начисляет
(флаг + `UNIQUE order_id`).

`apply_refunds(bot)`: `payments_refunds()` (применённый карточный заказ, у которого
`card_orders.status` стал `refunded` или `fraudulent`, а `payments.refunded_at` ещё пуст) →
`refunded_at=now`, `boosts.status='refunded'` по `order_ref`, и `unboost_listing` **только если**
возвращаемый Boost был последним для этого объявления (`latest_boost_for_listing`) — иначе человек
уже заплатил снова и снимать топ за прошлую покупку нельзя. `partial_refund` в выборку не попадает:
остаток денег настоящий, Boost остаётся. Итог — `events.boost_refunded`, ЛС владельцу («провайдер
сообщил о возврате… если это ошибка — /paysupport») и ЛС каждому админу со статусом «топ снят / топ не
тронут». Идемпотентно по `refunded_at`, поэтому повторная доставка `order_refunded` безопасна.

`check_manual_claims(bot)`: заявка «я перевёл» (`boosts.status='manual_pending'`) старше
`MANUAL_REMIND_HOURS=6` ч → админам повторно уходит тот же оффер с кнопками manapp/manrej и
`waited_hours`. Смысл: деньги по MIA уже лежат в банке, не хватает только галочки, а занятый админ
и «клиент, который не заплатил» выглядят одинаково. Дедуп — `events` (`name='manual_remind'`,
`meta=<boost_id>`), поэтому напоминание живёт ровно одно на окно и не заводится заново после рестарта.
Если `BOOST_MDL` пуст (рельс выключен), задача молчит.

`refund_manual_claim(bot, boost_id)` — не задача по расписанию, а функция, которую вызывает админская
кнопка `manrefyes:` (handler писать деньги не должен, и весь возврат живёт в одном модуле). Делает то же,
что `apply_refunds` для карты, но по `payments.telegram_charge_id = 'manual:<boost_id>'`, и потому что
у MIA нет API отзыва — по слову человека. Идемпотентна по `refunded_at`, отказывается по заявке, которая
ещё не подтверждена.

`check_webhook_silence(bot)` — сторож кассы, нужен из-за временного публичного адреса (туннель):
ссылка на оплату выдана **больше `SILENCE_MINUTES=15` мин назад**, а за это окно в `card_orders`
не пришло **ни одной** доставки ⇒ LS стучится в никуда (туннель пересоздался, nginx/веб-процесс упал,
секрет не совпал). Тогда `events.card_webhook_silent` + предупреждение каждому админу с текущим
`WEBAPP_URL`, повтор предупреждения — не чаще одного раза в `SILENCE_ALERT_COOLDOWN=1800` с
(счётчик в модуле: `_silence_alert_at` на `time.monotonic()`, то есть после рестарта бота он обнуляется).
Само правило вынесено в чистую функцию `is_webhook_silent(ago_seconds, deliveries)` — её можно
проверять без БД и без DM-рассылки (см. `deploy/test_card_rail.py`). Пока `CARD_ENABLED=False`,
сторож молчит: выключенный рельс не может «ничего не дополучить».

`notify_expired_boosts(bot)`: `boosts_that_just_expired()` — активные объявления, у которых
`boosted_until` прошёл не больше 48 ч назад и про которые ещё не писали. Владелец получает
«Boost закончился, объявление снова в общем списке» + `boost_pitch()` + `boost_kb`. Это самая дешёвая
повторная продажа в проекте и единственный момент, когда ценность топа ощущается. Дедуп — `events`
(`name='boost_expired'`, `created_at > boosted_until`): покупка нового Boost двигает `boosted_until`
и напоминание взводится снова, отдельного флага в таблице нет.

---

## 10. Развёртывание с нуля (чистый Ubuntu 24.04, один прогон)

Ниже — полный путь от «сервера нет» до живого бота. Все команды от root.

```bash
# 1. пакеты и структура
apt update && apt install -y python3.12-venv unzip curl
mkdir -p /opt/imobiliare/data/photos
# 2. venv + зависимости
python3 -m venv /opt/imobiliare/venv
/opt/imobiliare/venv/bin/pip install --upgrade pip
/opt/imobiliare/venv/bin/pip install aiogram aiosqlite aiohttp python-dotenv
# (или: pip install -r bot/requirements.txt после копирования кода)
# 3. код (см. §11: копированием по scp; на сервере git не настроен)
#    /opt/imobiliare/bot/{config.py,constants.py,db.py,keyboards.py,render.py,publish.py,lemon.py,
#                         scheduler.py,main.py,requirements.txt,handlers/*.py,webapp/app.py,webapp/static/index.html}
# 4. .env — ключи и значения бери из таблицы §4, токен из @BotFather
cat > /opt/imobiliare/bot/.env <<'EOF'
BOT_TOKEN=<токен от BotFather>
ADMIN_IDS=1182157446
DB_PATH=/opt/imobiliare/data/imobiliare.db
PHOTOS_DIR=/opt/imobiliare/data/photos
DEFAULT_CURRENCY=EUR
BOOST_STARS=400
BOOST_DAYS=7
BOOST_MDL=100
PAY_PHONE=<номер телефона получателя для MIA, +373…>
PAY_NAME=<имя получателя, как его видит банк>
PAY_INFO=<карта/IBAN, банк — если принимаете и так>
LS_API_KEY=<ключ API Lemon Squeezy, Settings -> API>
LS_STORE_ID=<id магазина>
LS_VARIANT_ID=<id варианта товара>
LS_WEBHOOK_SECRET=<signing secret из Settings -> Webhooks>
CARD_PRICE_CENTS=500
NUDGE_AFTER_DAYS=2
NUDGE_COOLDOWN_DAYS=4
NUDGE_INTERVAL_HOURS=6
CHANNEL_ID=@gaseste_casa
BOT_USERNAME=gaseste_casa_bot
WEBAPP_URL=
EOF
chmod 600 /opt/imobiliare/bot/.env
# 5. systemd (файлы лежат в deploy/)
cp deploy/imobiliare.service deploy/imobiliare-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now imobiliare imobiliare-web
# 6. проверка
systemctl is-active imobiliare imobiliare-web
journalctl -u imobiliare -n 20 --no-pager -o cat
curl -s http://127.0.0.1:8085/api/stats      # -> {"active":N}
bash /tmp/status.sh                            # см. §11
```

**Первичная настройка в Telegram (шаги 1–3 делает человек, бот их не может; шаг 4 — код):**

1. @BotFather: `newbot` (или взять существующего `@gaseste_casa_bot`), имя «Găsește Casa | Аренда Кишинёв»,
   короткое имя; токен → `.env`.
2. Создать **публичный канал** `t.me/gaseste_casa`, добавить бота **админом** с правом публикации,
   в `.env` `CHANNEL_ID=@gaseste_casa`, `BOT_USERNAME=gaseste_casa_bot`, рестарт.
   (Если канал приватный — нужен id вида `-100…`.)
3. Админ должен нажать `/start` в боте, иначе у него нет строки в `users` и демо-объявление создать нечем.
4. Пост-визитка в канал и её закрепление: `deploy/post_promo.py` (на проде это message_id **4**;
   `python /tmp/post_promo.py 4` **редактирует** существующую визитку на месте, а не постит дубль —
   цену Boost скрипт берёт из `keyboards.boost_pitch()`, поэтому после смены цены достаточно
   перевыполнить эту команду). Там же `deploy/set_menu_button.py` вешает кнопку Mini App в меню чата —
   это Bot API, а не @BotFather; звать его снова каждый раз, когда поменялся публичный адрес.

**Публичный HTTPS (нужен и Mini App, и карточному рельсу).** Есть два пути — временный бесплатный и
постоянный. Оба делают одно: наружу открывается `https://…/webhook/lemonsqueezy` и `WEBAPP_URL` в `.env`
перезаписывается на публичный адрес.

*Временный, без домена (один шаг, 0 €, адрес случайный):*

```bash
scp deploy/apply_tunnel.sh root@<IP>:/tmp/
ssh root@<IP> "sed -i 's/\r$//' /tmp/apply_tunnel.sh && bash /tmp/apply_tunnel.sh"
```

Скрипт скачивает статический бинарник `cloudflared` в `/usr/local/bin`, пишет unit
`cloudflared-tunnel.service` (`cloudflared tunnel --url http://127.0.0.1:8085 --no-autoupdate`,
`Restart=always`), берёт появившийся в журнале адрес `https://<случайное>.trycloudflare.com`, вписывает
его в `WEBAPP_URL`, перезапускает оба сервиса и проверяет снаружи `GET /api/stats` и **401** на вебхуке.
Входящих портов не добавляет (туннель исходящий), `ufw` можно не трогать; если постоянного домена не
будет, лишние `ufw delete allow 'Nginx Full'` вернёт сервер к «открыт только SSH».
**Обязательное следствие:** адрес меняется при каждом рестарте туннеля/сервера, поэтому после рестарта
надо (а) перезалить адрес в LS Settings → Webhooks и (б) перезапустить `bash /tmp/apply_tunnel.sh`,
чтобы `.env` снова показывал правду. Контроль «деньги у LS, а Boost не включился» — сторож из §9.

*Постоянный (домен с A-записью на IP VPS):*

```bash
scp deploy/apply_https.sh root@<IP>:/tmp/
ssh root@<IP> "sed -i 's/\r$//' /tmp/apply_https.sh && bash /tmp/apply_https.sh imobil.<зона>"
```

Скрипт ставит nginx + certbot (если их нет), пишет site-конфиг (`/photos/` → `PHOTOS_DIR`, остальное →
`127.0.0.1:8085`), снимает дефолтный сайт, `ufw allow 'Nginx Full'`, выпускает сертификат
(`certbot --nginx --redirect`), проверяет `https://<домен>/api/stats` и ждёт **401** от
`/webhook/lemonsqueezy` (маркер «публично доступно + TLS живой + подпись требует»), правит `WEBAPP_URL`
в `.env` и перезапускает бота. После — `python /tmp/set_menu_button.py`: кнопка Mini App в меню чата
ставится Bot API (`setChatMenuButton`), @BotFather для этого не нужен. Ручной путь без скрипта:
шаблон `deploy/nginx-chirie.conf` (заменить `imobil.REPLACE_ME.md`).

После **любого** из двух путей (туннель или домен) — быстрая сверка, что дверь действительно открыта:

```bash
scp deploy/check_public.sh root@<IP>:/tmp/
ssh root@<IP> "sed -i 's/\r$//' /tmp/check_public.sh && bash /tmp/check_public.sh"
```

Три сервиса `active`, `GET /` = 200, `GET /api/stats` = 200, `POST /webhook/lemonsqueezy` = **401**
(это правильный ответ: подписи нет), `/photos/` = 403, плюс очередь `card_orders`. Скрипт печатает
карту симптомов (000 → поднимать туннель, 404 → рестарт `imobiliare-web`, «всё зелёное, а денег нет» →
перерегистрировать вебхук).

**Бэкап денег (один шаг, сразу после первого деплоя — до первых платежей).**

```bash
scp deploy/apply_backup.sh root@<IP>:/tmp/
ssh root@<IP> "sed -i 's/\r$//' /tmp/apply_backup.sh && bash /tmp/apply_backup.sh"
```

Кладёт `/opt/imobiliare/backup.py` и `/etc/cron.d/imobiliare-backup` (03:00, ротация 14 дней), прогоняет
первый снимок сразу же и печатает `integrity ok` + счётчики (`listings/payments/card_orders`) — без этой
строки считать, что бэкап «есть», нельзя. Снимает и `.env` (права `0600`), и `photos-latest.tgz`.
Идемпотентен: можно перезапускать. Подробности — §12.

**Настройка Lemon Squeezy (частично делает человек, остальное — код):**

1. Аккаунт: `lemonsqueezy.com` → Sign up (email/пароль или Google) → письмо подтверждения → ссылка.
2. Создать **store** (имя + subdomain вида `my-store.lemonsqueezy.com`). Аккаунт сразу в **Test mode** —
   в тестовом режиме можно создавать товары и проводить тестовые покупки, настоящих денег нет.
3. **Products** → один товар «Boost» (любая цена — мы переопределяем её `custom_price`; тип — цифровой
   товар). Скопировать **Variant ID**.
4. `Settings → API` → **Generate key** → `LS_API_KEY`. id магазина — из URL дашборда → `LS_STORE_ID`.
   Эти два значения + `LS_VARIANT_ID` (3 числа/строки) приходят мне, я вписываю в `.env`.
5. **Вебхук регистрирует код, не человек:** `bash /tmp/apply_tunnel.sh` (публичный адрес) →
   `/opt/imobiliare/venv/bin/python /tmp/register_ls_webhook.py`. Скрипт по API LS
   (`POST /v1/webhooks`, документация проверена 10.10) создаёт **два** вебхука — тестовый и боевой —
   на `https://<адрес>/webhook/lemonsqueezy` с событиями `order_created, order_processed,
   order_refunded` (если LS отклоняет список событий — автоматический откат на
   `order_created, order_refunded`), генерирует signing secret, пишет его в `.env` как
   `LS_WEBHOOK_SECRET`, удаляет вебхуки, оставшиеся на прежнем (уже мёртвом) адресе туннеля,
   проверяет создание checkout реальным запросом к LS и перезапускает оба сервиса. Второй смысл
   двух вебхуков: при переходе магазина из test mode в live перерегистрация не нужна.
6. **Активация магазина (блокер человека, и только для настоящих денег).** По документации LS
   новый магазин торгует по-настоящему только после `Activate your store`: анкета о бизнесе +
   верификация личности (KYC/KYB), проверка обычно 1–2 рабочих дня. Политика LS прямо говорит: обычно
   **не одобряют** магазины, чей товар выполняется вне LS (услуги, аренда, «доступ к приватной
   telegram-группе»), и **одобряют** цифровые товары, подписки и лицензионные ключи. Наша честная
   формулировка в анкете: цифровой товар «Boost — поднятие объявления в топе на 7 дней», выдача
   автоматическая внутри нашего приложения, к заказу прикладывается лицензионный ключ (в LS у товара
   включается license keys) — физической поставки и доступа в закрытые группы нет.
   До активации всё остальное уже работает: тестовые покупки через `test_mode=1` начисляют Boost и
   не попадают в `REAL_REVENUE`.
7. `Banking`/`Payouts` (`Settings → Payouts`) → PayPal или банковский счёт; Молдова в списке стран
   выплат мерчанту. Включается после активации.
8. Проверка без денег: `deploy/test_card_rail.py` (наш, без аккаунта) и тестовая покупка через LS.

**План Б, если LS откажет (решение не код, а провайдер):** рельс **MIA** (§8, рельс 2) уже написан и не
требует никого — первый реальный доллар может прийти по нему, это самый быстрый путь вообще без
провайдера. Второй вариант — другой merchant of record с тем же контрактом «checkout + подписанный
вебхук», правка только в `lemon.py`. Из этого списка **Polar.sh исключён**: его AUP от 25.03.2026
запрещает маркетплейсы и «directories and boards», а наш продукт — доска чужих объявлений (§8
«Почему выбраны эти способы оплаты»). Локальный эквайринг (maib Mastercard Gateway, bpay, Fastpay) —
тот же план Б, но только после открытия ÎI/SRL.

Без шагов 1–4 кнопка «💳 Картой» просто не появляется (`CARD_ENABLED=False`), клиент видит MDL и Stars —
рельсы работают.

Безопасность (теперь нарушено, чинить при случае): оба сервиса идут от `root`; нужен отдельный
`User=` (вместе с ним починятся права `.env`, сейчас файл читается всеми); вход по ключу с рабочей
машины **настроен и проверен** (`ssh -o BatchMode=yes` проходит, `authorized_keys` непустой), но
`PermitRootLogin yes` + `PasswordAuthentication yes` на порту 22 = root с паролем снаружи (чинит
`deploy/harden_ssh.sh`, §12); подача объявлений без rate-limit (один человек может засыпать доску);
`imobiliare-web` слушает только 127.0.0.1 и наружу не смотрит. Бэкапы **больше не отсутствуют** —
cron 03:00 стоит (§10, §12).

---

## 11. Деплой изменений и проверка (рабочая рутина)

Код на сервере = копия локального (git на сервере нет). Один цикл:

```powershell
# из c:\WORK\Projects\onedollorproject (PowerShell: && НЕ работает, разделитель ;)
scp bot/config.py bot/db.py root@<IP>:/opt/imobiliare/bot/
scp bot/handlers/boost.py root@<IP>:/opt/imobiliare/bot/handlers/
scp deploy/verify_manual.sh root@<IP>:/tmp/
ssh root@<IP> "sed -i 's/\r$//' /tmp/verify_manual.sh && bash /tmp/verify_manual.sh"
```

```bash
# обязательная проверка компиляции перед рестартом (делают apply_manual.sh / apply_card.sh)
cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python -m py_compile \
  config.py constants.py db.py keyboards.py scheduler.py publish.py render.py main.py lemon.py \
  handlers/*.py webapp/app.py
systemctl restart imobiliare imobiliare-web && sleep 4
systemctl is-active imobiliare imobiliare-web && curl -s http://127.0.0.1:8085/api/stats
journalctl -u imobiliare -n 20 --no-pager -o cat
journalctl -u imobiliare-web -n 20 --no-pager   # TypeError здесь = битый app.py, откатывать надо оба сервиса
```

**Полный синк (когда локальный код и прод разошлись и нужна гарантированная идентичность):** scp-ом
все runtime-файлы из §2 (`bot/*.py`, `bot/handlers/*.py`, `bot/webapp/app.py`, `bot/webapp/static/index.html`),
затем блок компиляции выше и рестарт **обоих** сервисов. `bot/.env` на сервере не трогается — он вне репозитория.
Размеры файлов должны совпадать с локальными (`ls -l` на сервере против `Get-ChildItem bot/*.py`) — это самая
дешёвая проверка, что scp не оборвался.

**Железное правило PowerShell:** никогда не всовывать питон/JS внутрь `ssh "... python -c '...'"` —
PowerShell ломает кавычки и `[`. Всегда: файл `.sh`/`.py` → scp → `sed -i 's/\r$//'` → `bash`/`python`.

Готовые скрипты: `deploy/status.sh` (сервисы + счётчики БД + ключи .env + webapp + лог),
`deploy/apply_manual.sh` / `verify_manual.sh` (рельс MDL: registry обработчиков, схема, статусы),
`deploy/apply_nudge.sh` / `verify_nudge.sh` (нудж), `deploy/cleanup_test.sh`,
**`deploy/apply_card.sh`** (карточный рельс: компиляция включая `lemon.py`, дописывает 5 LS-ключей в
`.env`, рестартит **сначала бота** (он владеет схемой), затем веб; печатает `CARD_ENABLED`, список
обработчиков `boost`, `rails_line()`, параметры сторожа и текущий `WEBAPP_URL`, схему
`card_orders`/`payments` и делает curl вебхука — **401**
означает «роут живой, подпись требует»), **`deploy/apply_https.sh <домен>`** и
**`deploy/apply_tunnel.sh`** (см. §10 — временный публичный HTTPS без домена; перезапускать после
каждого рестарта туннеля, он печатает текущий адрес и сам правит `WEBAPP_URL`) и
**`deploy/register_ls_webhook.py`** (регистрирует вебхук в LS через API: создаёт тестовый+боевой,
генерит и пишет `LS_WEBHOOK_SECRET`, чистит вебхуки на старом адресе, проверяет checkout, рестартует
сервисы; без `LS_API_KEY`/`LS_STORE_ID`/https-`WEBAPP_URL` корректно отказывается что-либо трогать).

Тесты и боевые прогоны (запускаются на сервере, самоочищаются):

- `deploy/test_money_path.py` — вызывает **боевой** `scheduler.nudge_unboosted`, админ получает ЛС
  с рабочей кнопкой; потом чистит `listings.nudged_at` и тестовые события;
- **`deploy/test_card_rail.py`** — **тест кассы без аккаунта LS и без денег** (название историческое,
  сейчас он покрывает весь денежный путь): создаёт тестовое объявление, подделывает
  `order_created` с честной HMAC-подписью, гонит его через
  `verify_signature` → `parse_order_event` → `store_card_order` (повтор заказа = no-op) →
  `scheduler.apply_card_orders` и утверждает `boosted_until`, строку `payments` (5.0 USD,
  `boost_card`, `is_test=0`), `boosts.status='paid'` + `order_ref='ls:<order_id>'`,
  `stats().revenue_card` и `card_unapplied=0`; потом **обратную сторону денег**: `status='refunded'` →
  `apply_refunds` снимает топ, ставит `boosts.status='refunded'`, `payments.refunded_at` и возвращает
  `revenue_card` к прежнему значению, повторная доставка возврата = no-op, `partial_refund` не трогает
  ничего; затем `notify_expired_boosts` (ровно одно предложение на окно истекания) и
  `check_manual_claims` (напоминание о неподтверждённом MIA-переводе не чаще одного раза за
  `MANUAL_REMIND_HOURS`); затем **обратная сторона рельса 2**: `refund_manual_claim` по подтверждённому
  переводу — `revenue_mdl` уменьшается на `BOOST_MDL`, `boosts.status='refunded'`, топ снят, повторный
  тап отвечает «уже отмечен» и ничего не меняет, а по неоплаченной заявке функция отказывается что-либо
  делать; плюс сторож (`is_webhook_silent` на четырёх наборах входных данных и один
  проход `check_webhook_silence`, который на здоровом рельсе должен вернуть `False`, то есть писать
  админам не должен). После всего тест удаляет свои строки. Успех = `✅ MONEY RAIL OK`;
  **важно:** возвраты и напоминания шлют настоящие ЛС админу — это часть проверяемой цепочки, так что
  прогон теста на проде означает несколько сообщений тебе в Telegram;
- `deploy/make_demo.py` — удаляет старые ДЕМО-карточки, создаёт активную «ДЕМО» карточку на админе
  и шлёт админу настоящий Boost-оффер (все включённые кнопки). Не публикуется в канал;
- `deploy/post_promo.py [message_id]` — пост-визитка в канал + пин; с аргументом **редактирует**
  существующий пост вместо дубля.

**Тест MIA-рельса без денег:** ДЕМО-карточка → «💰 MIA 100 MDL» (или «💰 Перевод 100 MDL», пока
`PAY_PHONE` пустой) → «💸 Я перевёл 100 MDL» → админу приходит заявка (сумма, район и цена объявления,
**номер телефона плательщика**, референс `Boost #id`) → «✅ Деньги пришли» → Boost активен,
`/stats` показывает `REAL_REVENUE MDL 💰: 100`. Это **не** реальные деньги; чтобы не врать себе в метрике,
снеси тестовые строки:

```bash
/opt/imobiliare/venv/bin/python - <<'PYEOF'
import sqlite3
d = sqlite3.connect('/opt/imobiliare/data/imobiliare.db')
d.execute("delete from payments where purpose='boost_manual'")
d.execute("delete from boosts where status='manual'")
d.execute("update listings set boosted_until=NULL where description like '%ДЕМО%'")
d.commit()
print("payments left:", d.execute("select count(*) from payments").fetchone())
PYEOF
```

---

## 12. Эксплуатация, бэкап, откат

Логи: `journalctl -u imobiliare -f`, `journalctl -u imobiliare-web -f`.
При рестарте бот шлёт всем админам «✅ ChirieBot запущен и готов к работе» — это и есть health-check.

```bash
# БЭКАП УЖЕ СТОИТ НА СЕРВЕРЕ (установлен 11.10): deploy/apply_backup.sh кладёт /opt/imobiliare/backup.py
# и /etc/cron.d/imobiliare-backup (03:00), потом печатает список снимков. Ручной прогон:
/opt/imobiliare/venv/bin/python /opt/imobiliare/backup.py
ls -lh /opt/imobiliare/data/backups | tail        # imobiliare-YYYYmmdd-HHMMSS.db + .env + photos-latest.tgz
```

Что делает `backup.py` (и почему не «cp файла БД»): снимок через `sqlite3` backup API — единственный
консистентный способ при WAL; затем `PRAGMA integrity_check` и счётчики строк в тот же лог; `.env`
копируется рядом с правами `0600` (без токенов восстановление невозможно); фото — `photos-latest.tgz`;
ротация — старые снимки старше `KEEP_DAYS=14` удаляются (вместе с их `.env`-двойниками). В cron-строке
вывод пишется в `/var/log/imobiliare-backup.log` — туда и смотреть, если кажется, что бэкапа нет.
Проверка на проде 11.10:
`imobiliare-20261011-014546.db 49152 bytes | integrity ok`, cron-файл на месте.

Ручной разовый снимок, если нужен «вот сейчас»:

```bash
/opt/imobiliare/venv/bin/python -c "import sqlite3;s=sqlite3.connect('/opt/imobiliare/data/imobiliare.db');\
d=sqlite3.connect('/opt/imobiliare/data/backup.db');s.backup(d);print('ok')"   # sqlite3 CLI НЕ установлен
```

Восстановление из бэкапа: остановить оба сервиса → положить файл на `DB_PATH` → проверить
`select count(*) from listings/payments` → запустить (точные команды печатает `apply_backup.sh`).
Фото — отдельный каталог: `tar xzf /opt/imobiliare/data/backups/photos-latest.tgz -C /opt/imobiliare/data`
(без них карточки в канале останутся, Mini App потеряет картинки; `file_id` в БД позволяют докачать).

Откат кода: на сервере git нет — значит копируем нужную ревизию локально
(`git checkout <sha> -- bot/`) и scp-ом сверху, затем рестарт.
История коммитов и есть источник правды: `git log --oneline`.

**Сверка «прод = git» (деплой же копированием, drift возможен).** `run.ps1` стрипает CRLF, а `.gitattributes`
держит в репозитории LF, поэтому сравнивать надо LF-байты, а не рабочий Windows-файл как есть. С одной
стороны: `powershell -File deploy/run.ps1 bot/config.py "md5sum /opt/imobiliare/bot/*.py
/opt/imobiliare/bot/handlers/*.py"`; с другой — те же файлы локально, нормализованные к LF:
`foreach ($f in (gci bot -r -f -i *.py)) { $b=[Text.Encoding]::UTF8.GetBytes(([IO.File]::ReadAllText($f) -replace "\r\n","\n"));
"$((Get-FileHash -Algorithm MD5 -InputStream [IO.MemoryStream]::new($b)).Hash.ToLower())  $($f.Name)" }`
— хеши должны совпасть точь-в-точь (проверено 11.10: scheduler/db/keyboards/admin сходятся до байта).

**Кто стучится в SSH.** Сервисы и `.env` живут под root, а `sshd -T` на сервере даёт
`permitrootlogin yes` + `passwordauthentication yes` на 22 порту — то есть наружу открыт вход «root +
пароль» для машины, которая знает BOT_TOKEN и секрет вебхука. `deploy/harden_ssh.sh` (выполнен 11.10)
ставит fail2ban с дроп-ином `/etc/fail2ban/jail.d/imobiliare.conf`: бан 1 ч после 5 неудач за 10 мин,
`backend = systemd`, а адрес, с которого мы сами подключаемся (`$SSH_CLIENT`), попадает в `ignoreip` —
бан, отрезающий единственного админа, хуже отсутствующего бана. Проверено: `fail2ban-client status sshd`
→ jail активен, 0 banned; `systemctl is-active ssh` → active. Перезапускать скрипт стоит, если
поменялся наш внешний IP (он запишет новый в `ignoreip`).

Следующий шаг (делать только с согласия владельца сервера): `bash /tmp/harden_ssh.sh
--disable-root-password` ⇒ `PermitRootLogin prohibit-password` через
`/etc/ssh/sshd_config.d/99-imobiliare.conf`. Скрипт сперва требует доказательство из журнала
(`Accepted publickey for root from <наш IP>`), потом `sshd -t`, потом `reload` (не `restart` — живые
сессии не рвутся), и печатает команду отката. Отключать пароль надо, **не закрывая текущее окно** и
проверяя новый вход из второго: это единственная операция проекта, которая может оставить человека без
доступа к его же VPS (спасает только консоль хостинг-провайдера).

---

## 13. Админ-команды бота (только для `ADMIN_IDS`)

| Команда | Действие |
|---|---|
| `/stats` | пользователи, объявления (активных/на модерации), Boost'ы, платежи (и число тестовых), `REAL_REVENUE Картой 💳` (+ сколько карточных заказов **не применено**), `REAL_REVENUE MDL 💰`, `REAL_REVENUE Stars ⭐` (возвраты уже вычтены из всех трёх), `Возвратов/чарджбеков`, `Переводов ждут подтверждения` — две строки «тут нужен человек» |
| `/pending` | до 20 карточек на модерации с кнопками одобрить/отклонить |
| `/nudge` | немедленно пройтись по двигателю выручки (сколько офферов отправлено) |
| `/broadcast <текст>` | рассылка всем пользователям кроме админов, пауза 0.05 с, `events.broadcast` с `ok/total` |
| `/admin` | список команд |
| `/paysupport` | контакт по возвратам (для всех); без `CONTACT_URL` — «напиши в этот чат», текст уходит админам через fallback.py |
| кнопки `manapp:`/`manrej:` | подтвердить/отклонить ручной MDL-платёж |
| кнопка `manref:` → `manrefyes:` | «деньги вернули переводом»: MDL уходят из выручки, Boost гаснет (§8 «Рельс 2») |

---

## 14. Решения и границы (почему именно так, не ломай без причины)

- **SQLite вместо Postgres:** одна машина, параллельных писателей два, весь масштаб — десятки тысяч строк.
  Миграции — аддитивные `ALTER TABLE` в `_migrate()` при старте, без Alembic.
- **polling вместо webhook (для бота):** боту не нужен TLS/порт, `Restart=always` перекрывает всё.
  Входящие HTTPS-запросы принимает только webapp-процесс, и то опционально: без домена он живёт на
  127.0.0.1, а карточный рельс просто выключен.
- **Boost = колонка `boosted_until`**, а не строка-слот: истёк сам — выпал из топа, никакой очистки не нужно.
- **Фото в двух видах:** `file_id` в БД (для бота) и `{id}_{i}.jpg` в `PHOTOS_DIR` (для Mini App и поста в канал),
  потому что `file_id` нельзя отдать веб-странице.
- **Никакого скрейпинга 999.md/otakoy** (MISSION: ToS + чужие фото). Наполнение только руками/через бота;
  шаблоны текстов — в `GROWTH.md`.
- **AI только для неструктурированного** (описания, классификация фото). Деньги, БД, платежи, конечные
  автоматы —
  детерминированный код. Сейчас AI не используется вообще.
- **Карточный рельс через Lemon Squeezy (merchant of record), а не self-acquiring:** после
  `PROVIDER_ACCOUNT_INVALID`/`PAYMENT_PROVIDER_INVALID` (§8) нужен был путь, где карта клиента не
  связана с Telegram и где нам не нужно юрлицо. LS поддерживает Молдову для выплат и сам отвечает за
  VAT/чарджбеки. Отклонённые варианты: **Stripe** (не даёт мерчант-аккаунт в MD), **maib/bpay/Fastpay**
  (нужен ÎI/SRL + договор), **crypto-эквайринг** (не «как у людей», лишняя юридика, узкая аудитория),
  **опрос списка заказов `GET /v1/orders`** (не отдаёт `custom_data` ⇒ нельзя понять, какой объект оплачен).
- **Вебхук ничего не решает про деньги.** Он только кладёт строку в `card_orders`; начисление делает
  `scheduler.apply_card_orders` — единственный писатель денег для карты. Иначе два процесса гонялись бы
  за `boosted_until`, а ретрай доставки удваивал бы Boost. Цена ошибки — реальные деньги, поэтому
  детерминированный SQL и идемпотентность по `order_id`, без «умной» логики.
- **Публичный HTTPS временно — бесплатный туннель, а вебхук регистрирует код.** Решение человека
  10.10: не покупать домен ради первого платежа, а поднять `apply_tunnel.sh` (cloudflared quick tunnel,
  0 €) и перейти на `apply_https.sh <домен>`, когда продукт начнёт окупаться. Цена — адрес меняется
  при рестарте, поэтому (а) `register_ls_webhook.py` пересоздаёт вебхук по API за секунды и чистит
  старые записи, (б) `check_webhook_silence` не даёт «деньги у провайдера, а Boost не включился»
  остаться незамеченным. Альтернативы, от которых отказались: покупать домен сразу (медленнее и
  дороже на нулевой выручке), «просто держать LS-ссылку без вебхука» (атрибуции заказа нет),
  пересылать платежи на e-mail/вручную (не «как у людей», и не масштабируется).
- **Одобрение LS — внешний риск, а не наш код.** Политика merchant of record: услуги и «то, что
  исполняется вне LS», обычно не одобряются; цифровые товары, подписки и лицензионные ключи —
  одобряются. Поэтому продукт позиционируется как цифровой (Boost + лицензионный ключ на заказ),
  анкета заполняется честно, а план Б (MDL-рельс и/или другой MoR) существует с самого начала —
  первый доллар не должен зависеть от решения одной комплаенс-команды.
- **MIA (НБМ) — рельс первой недели, а не «страховка».** Решение принято 11.10 после `PROVIDER_ACCOUNT_INVALID`
  и `PAYMENT_PROVIDER_INVALID` (§8): чтобы первый доллар не зависел от регистрации провайдера, нужен путь,
  где платёж совершается **в банковском приложении самого клиента**. MIA — национальная схема БНМ: перевод по
  номеру телефона, зачисление секунды, комиссии для переводящего нет, ни эквайринга, ни карты в Telegram, ни
  KYC. Цена рельса — ручное подтверждение админом (один тап) и юридическая оговорка: регулярные переводы
  физлицу = предпринимательская деятельность; после 5–20 продаж открывать **ÎI (ИП) / microapă** с патентом
  на услуги и уходить в местный эквайринг. Карточный рельс остаётся первым по удобству клиента и основным
  после запуска LS.
- **Активные рельсы зафиксированы: MIA + Telegram Stars; Lemon Squeezy намеренно припаркован (решение
  11.10 после проверки рынка, а не кода).** Смотрел, чем реально берут оплату молдавские доски и магазины
  (страница оплаты 999.md + обзор интеграции молдавского e-commerce): 999.md монетизирует продвижение через
  **пополняемый кошелёк**, а пополняют его картой, **MIA**, BPay, Paynet, myRunPay, QIWI, Moldcell, банко-
  вским переводом, терминалами и наличными — **ни Stripe, ни Lemon Squeezy, ни Paddle в местном ландшафте не
  фигурируют**. Данные AmCham/Datalinie: картой на сайте платят 11.9%, ramburs 65.4%, MIA 0.4% (но MIA —
  единственный мгновенный цифровой путь для физлица без ÎI). Выводы: (1) **LS у молдавского покупателя —
  terra incognita**, а зарубежный шлюз + местная карта часто не проходят 3-D Secure; (2) **«карта как у людей»
  в MD = maib/Victoriabank (1.8–3%) и они требуют ÎI/SRL + договор e-commerce** — физлицо приём карты на сайте
  делать не может. Поэтому **продуктовые рельсы недели: 💰 MIA (физлицо может, деньги на счёте за секунды) и
  ⭐ Stars (Telegram-нативно, один тап, без юрлица для приёма).** Карточный код LS остаётся в репозитории
  мёртвым грузом под `CARD_ENABLED=False` и включается только (а) под диаспору/зарубежную карту или (б) после
  открытия ÎI переходим на местный эквайринг. Модель баланса 999 (кошелёк → списание за топ) — в TODO как
  способ поднять повторные покупки, когда появятся продажи; до этого — раздувание.
- **Все три рельса стоят одинаковых денег.** 100 MDL ≈ $5.00 картой ≈ 400 Stars (вознаграждение мерчанту
  $0.013 за звезду, developer terms §6.2.4). Историческое `BOOST_STARS=50` (≈ $0.65) было дырой в 8 раз:
  клиент голосовал за самый дешёвый рельс, а мы считали это конверсией. Любая правка цены = правка
  **синхронно** в `CARD_PRICE_CENTS`, `BOOST_MDL`, `BOOST_STARS` (иначе паритет ломается снова).
- **Polar.sh отвергнут как платёжный рельс (проверено 11.10 по его AUP от 25.03.2026):** запрещены
  «marketplaces / продажа товаров и услуг других продавцов» и «любые продукты, позволяющие не-Polar
  продавцам продавать», а «directories and boards» отдельной строкой в restricted. Наш продукт — доска
  объявлений с чужими квартирами, то есть прямой кандидат на бан аккаунта; плюс ревью первой выплаты до
  14 дней. Отказ задокументирован, чтобы кто-нибудь не «вернул поляру от нечего делать».
- **Текст платёжной инструкции живёт в одном месте.** `keyboards.pay_lines()/pay_block()/manual_pitch()/
  manual_reference()/transfer_word()/transfer_button()` — чистые функции без БД и без aiogram-контекста,
  поэтому проверяются утверждениями (`deploy/apply_manual.sh`), а не ручным прогоном. Реквизиты из `.env`
  обязательно проходят `html.escape`: `parse_mode="HTML"` на строке с `<` или `&` иначе роняет отправку
  оффера. Никаких «100 MDL» и «Переведи на карту» в обработчиках и скриптах — только из этих функций.
- **Выручка по рельсам не суммируется в одну цифру.** `payments` хранит валюту как её прислал
  провайдер (`USD` от LS, `MDL` от перевода, `XTR` от звёзд), `/stats` печатает три отдельные строки и
  `card_unapplied`. Смешивать USD и MDL в «one revenue number» — значит врать себе в метрике.
- **Возврат денег обязан выключить продукт, и он привязан к оплате, а не к объявлению.** `boosts.order_ref`
  (`ls:<order_id>` / `tg:<charge_id>` / `manual:<boost_id>`) — единственное, что позволяет понять, **какой
  именно** Boost оплатил этот заказ: без него «снять топ по возврату» превращается в «снять топ, который
  человек мог оплатить снова». Поэтому топ снимается только если возвращаемый Boost последний
  (`latest_boost_for_listing`), `partial_refund` не снимает ничего, а повторная доставка вебхука
  безопасна благодаря `payments.refunded_at`. Всё это делает `apply_refunds` в том же **единственном**
  писателе денег, что и начисление (§9), — вебхук по-прежнему ничего не решает. Возврат MIA-перевода
  (`refund_manual_claim`) намеренно делает **те же три записи** (плюс тот же guard «только если Boost
  последний»), а не отдельную бухгалтерию: у MIA нет API отзыва, деньги возвращает человек в банке, но
  «деньги вернулись» для карты и для перевода обязаны означать одно и то же в БД — иначе MDL-строка
  выручки молча остаётся завышенной после каждого ручного возврата.
- **Дедупликация напоминаний живёт в `events`, а не в памяти процесса.** Счётчики в модуле
  (`_silence_alert_at`) после рестарта обнуляются, а обещание «не долбить админа один и тот же перевод
  каждые 60 секунд» от перезапуска не зависит: `event_recent(name, meta, hours)` + запись
  `manual_remind`/`boost_expired`. Для `boost_expired` роль метки играет сам `boosted_until` — покупка
  нового Boost двигает дату, и напоминание взводится заново без отдельной колонки.
- **Бэкап — часть продуктовой логики, а не «когда-нибудь».** `payments`/`boosts` восстановить нельзя
  (их нет нигде, кроме этой БД), объявления — можно. Поэтому `deploy/apply_backup.sh` ставит cron 03:00,
  снимок делается через `sqlite3` backup API (при WAL `cp` даёт рваный файл), каждый снимок проверяется
  `PRAGMA integrity_check`, рядом кладётся `.env` (без токенов восстановление = новый бот и новый
  вебхук) и `photos-latest.tgz`, ротация 14 дней, и **первый прогон выполняется сразу при установке**:
  непроверенный бэкап — не бэкап.
- **Бюджет этапа ≤ €100**, хостинг — уже оплаченный VPS (Time4VPS), домен пока не куплен.
- **Секретов в git нет** (`.gitignore` закрывает `.env`, `ACCESS.md`, `*.db`, фото). BOT_TOKEN когда-то
  попал в три deploy-скрипта и в историю — вырезан кодом **и** переписан историей
  (`git filter-branch` + `reflog expire` + `gc --prune=now`) **до** первого пуша; на всякий случай
  токен стоит отозвать через @BotFather.

---

## 15. Текущее состояние и чего не хватает (синхронно с STATUS.md)

Прод: VPS Ubuntu 24.04, `imobiliare` + `imobiliare-web` + `cloudflared-tunnel` active (все три),
бот `@gaseste_casa_bot` (id 8763027850), канал `t.me/gaseste_casa` подключён и автопостит,
закрепление визитки (message_id 4), демо-карточка пересоздана (последняя — #10, «Центр, 450 EUR»),
реальных объявлений **0**, платежей **0**, `REAL_REVENUE = 0`, `card_orders` пуст.
**`WEBAPP_URL = https://broker-licensing-jvc-jay.trycloudflare.com` — витрина и ящик вебхука открыты в
интернет** (`check_public.sh`: `/` 200, `/api/stats` 200, `POST /webhook/lemonsqueezy` 401, `/photos/` 403).
nginx 1.24 + certbot 2.9 **установлены**, `ufw allow 'Nginx Full'` открыт, порт 80 отвечает снаружи 200 —
не хватает только hostname с A-записью (постоянный HTTPS вместо туннеля). Карточный рельс **задеплоен и
проверен тестом без денег** (`test_card_rail.py` → `✅ MONEY RAIL OK`: начисление, возврат,
`partial_refund`, ручной возврат MIA, истечение Boost, напоминание о переводе, сторож), но
`CARD_ENABLED=False`: нет аккаунта Lemon Squeezy, поэтому кнопка «💳 Картой» клиенту ещё не видна.
MIA-рельс задеплоен и проверен (`apply_manual.sh`): цены 100 MDL / 400 Stars, обе ветки инструкции и
HTML-экранирование под самотестом; `PAY_PHONE`/`PAY_NAME` пустые, поэтому покупатель пока видит
«реквизиты пришлёт модератор». Возврат такого перевода правится кнопкой `manref:`
(`scheduler.refund_manual_claim`, §8 «Рельс 2») — MDL уходят из выручки, а Boost с топа снимается.
Закрыто 11.10: визитка в канале отредактирована на актуальную цену
(`post_promo.py 4`), кнопка Mini App в меню чата поставлена кодом (`set_menu_button.py`), ежедневный
бэкап БД+`.env`+фото стоит на cron (`apply_backup.sh`, первый снимок проверен `integrity_check`).

Не сделано, по приоритету:

1. `PAY_PHONE` + `PAY_NAME` пустые и **MIA не активирована в банке владельца** — самый быстрый рельс
   (провайдер не нужен) не может принять деньги: две строки в `.env` + один клик в банковском приложении,
   это человеческий шаг (§10, §8 «Рельс 2»).
2. Нет аккаунта/ключей **Lemon Squeezy** (`LS_API_KEY/LS_STORE_ID/LS_VARIANT_ID`) — кнопка «💳 Картой»
   не появляется (человеческий шаг, §10). Следом за ним такой же человеческий шаг — **активация
   магазина** (анкета + KYC, 1–2 рабочих дня); без неё тестовые покупки работают, настоящих денег нет,
   и есть риск отказа именно из-за «услугового» типа товара (§10 «План Б», §14).
3. 0 реальных объявлений на доске — главный блокер, не код (нужно 3–5, потом 50–100).
4. Публичный HTTPS есть, но **временный**: адрес cloudflared меняется при рестарте туннеля ⇒ после любого
   рестарта нужны `apply_tunnel.sh` + `register_ls_webhook.py` + `set_menu_button.py`, молчание вебхука
   ловит сторож `check_webhook_silence` (§9). Постоянный вариант — домен + `apply_https.sh`.
5. Гигиена сервера: оба сервиса работают от **root**, нет rate-limit на подачу объявлений (один человек
   может засыпать доску), `.env` доступен широко (сервисы же от root), **SSH всё ещё принимает root по
   паролю** (`permitrootlogin yes` + `passwordauthentication yes`) — снимается `harden_ssh.sh
   --disable-root-password`, но это шаг владельца, а не мой (§12). fail2ban уже стоит.
6. Нет отчёта по источникам трафика (`events.start.meta` уже пишет метки — данные есть, отчёта нет).
7. Вывод денег: LS платит на банковский счёт после верификации аккаунта (Banking); звёзды — только через
   Fragment в GRAM; местный эквайринг MD и «нормальный учёт» требуют ÎI/SRL — теперь это опция, а не блокер.
   Возврат по MIA по-прежнему делает человек в банковском приложении (у схемы нет API отзыва), но
   бухгалтерия после этого правится кнопкой `manref:` (§8 «Рельс 2»).

---

## 16. «Всё пропало»: порядок восстановления по пунктам

1. Прислать/найти: доступ к VPS (`ACCESS.md`), аккаунт Telegram с правами админа канала и бота.
2. `git clone https://github.com/shevchenkovania-stack/gaseste_casa.git` (репозиторий приватный)
   или, если репозиторий потерян: код есть на сервере в `/opt/imobiliare/bot/` — забрать `scp -r`.
3. §10 шаги 1–2 (venv + requirements).
4. Если БД цела — просто скопировать `/opt/imobiliare/data/` и продолжить с шага 6.
   Если БД потеряна, но жив сервер: самый свежий снимок в `/opt/imobiliare/data/backups/imobiliare-*.db`
   (`ls -1 ... | tail -1`) → положить на `DB_PATH`, рядом `.env`-двойник (`<снимок>.db.env`), снять
   `photos-latest.tgz`; точные команды печатает `deploy/apply_backup.sh` в конце установки.
   Если потеряно и это — создать пустую (сделается при старте), затем: `deploy/seed.py` для
   демо-наполнения, объявления владельцев придётся собирать заново, а **историю платежей восстановить
   нельзя** (отсюда и cron-бэкап).
5. `.env` по таблице §4; токен — из @BotFather (если потерян, `revoke` и новый), `ADMIN_IDS` — твой tg id
   (узнать: напиши боту /start, потом `select tg_id from users`), `CHANNEL_ID=@gaseste_casa`,
   `BOT_USERNAME=gaseste_casa_bot`, `BOOST_*`, `NUDGE_*`, **`PAY_PHONE`/`PAY_NAME`/`PAY_INFO`** (без них
   MIA-рельс не может показать реквизиты), **`LS_*` + `CARD_PRICE_CENTS`**
   (ключи LS лежат в дашборде; `LS_WEBHOOK_SECRET` должен совпадать со значением в Settings → Webhooks,
   иначе все вебхуки будут отдавать 401 и деньги перестанут начисляться).
6. §10 шаг 5 (systemd units, enable --now) + §11 проверка (`status.sh`).
7. Если канал «потерян» (бот вылетел из админов) — вернуть админку в Telegram вручную, проверить постом.
8. Прогнать тесты без денег (§11): `test_card_rail.py` (весь денежный путь: начисление, возврат,
   истечение, напоминание, сторож ⇒ `✅ MONEY RAIL OK`) и ручной MDL-прогон через ДЕМО-карточку,
   затем `/nudge` — убедиться, что выручка считается по всем трём рельсам. На новом сервере отдельно
   `bash /tmp/apply_backup.sh` (cron-бэкап — его на свежепокупной машине нет).
9. Если потерян домен/сертификат: купить или восстановить A-запись → `bash /tmp/apply_https.sh <домен>` →
   `python /tmp/register_ls_webhook.py` (вебхук на новый адрес создаёт код, руками в дашборде —
   только если нет `LS_API_KEY`). Быстрый бесплатный эквивалент без домена: `bash /tmp/apply_tunnel.sh`
   (тот же результат — `WEBAPP_URL` + проверка 401, но адрес новый при каждом запуске).
   **`card_unapplied > 0` в `/stats` = заказы пришли, но не начислились** — чинить по §8.
   **Админу пришло «⚠️ Касса молчит»** = ссылки на оплату выдаются, доставок нет: смотреть
   `journalctl -u imobiliare-web` (401 = секрет/подпись, пусто = URL в LS не тот или туннель умер).

## 17. Ключевые якоря (числа, которые легко забыть)

| Что | Значение |
|---|---|
| Бот | `@gaseste_casa_bot`, tg bot id `8763027850`, имя «Găsește Casa \| Аренда Кишинёв» |
| Канал | `@gaseste_casa` / `t.me/gaseste_casa`, бот — админ, пост-визитка = message_id `4` (закреплён) |
| Админ | `ADMIN_IDS = 1182157446` |
| Пути | код `/opt/imobiliare/bot`, venv `/opt/imobiliare/venv`, данные `/opt/imobiliare/data` |
| Сервисы | `imobiliare` (бот), `imobiliare-web` (Mini App + вебхук, `127.0.0.1:8085`), `cloudflared-tunnel` (публичный HTTPS) |
| Продукт | Boost на `BOOST_DAYS=7` дней, **одна цена на три рельса**: **картой `CARD_PRICE_CENTS=500` = $5.00** (LS), **`BOOST_MDL=100` MDL через MIA**, **`BOOST_STARS=400` ⭐** (вознаграждение мерчанту $0.013/звезда ⇒ ≈ $5.20) |
| MIA (НБМ) | перевод по номеру телефона в приложении любого банка Молдовы; получатель = `PAY_PHONE` + `PAY_NAME`, комментарий-референс `Boost #<listing_id>`, подтверждает админ (`manapp:`/`manrej:`) |
| Mini App в меню | `deploy/set_menu_button.py` → Bot API `setChatMenuButton` (не @BotFather); текст кнопки «🏠 Искать квартиры», URL = `WEBAPP_URL`; Telegram нормализует адрес до `<url>/` |
| Вебхук карты | `POST /webhook/lemonsqueezy`, подпись `X-Signature` (HMAC-SHA256 hex от raw body), события `order_created/order_processed/order_refunded`, `custom_data = {listing_id, tg_id}` |
| LS-аккаунт | store + **один** товар (цена переопределяется `custom_price`); id магазина и variant id → `.env` |
| Активация LS | новый магазин всегда в **test mode**; настоящая торговля — только после `Activate your store` (анкета + верификация личности, обычно 1–2 рабочих дня); услуги/аренду и «доступ в telegram-группы» LS обычно **не** одобряет ⇒ товар подаём как цифровой + license key |
| Регистрация вебхука | `deploy/register_ls_webhook.py` → `POST /v1/webhooks` (тестовый **и** боевой, `secret = secrets.token_hex(24)` → `.env`) + `DELETE /v1/webhooks/{id}` для старых адресов туннеля |
| Публичный HTTPS | временно cloudflared quick tunnel: unit `cloudflared-tunnel`, `https://<случайное>.trycloudflare.com`, адрес меняется при рестарте. Постоянно: `apply_https.sh <домен>` (nginx + certbot) |
| Сторож кассы | `SILENCE_MINUTES=15` (ссылка дана, доставок нет) ⇒ `events.card_webhook_silent` + ЛС админам; повтор не чаще `SILENCE_ALERT_COOLDOWN=1800` с |
| Checkout | живёт `POST /v1/checkouts`, `expires_at` = **+3 ч** (`expires_minutes=180`), ссылка отдаётся URL-кнопкой |
| Двигатель | нудж: после 2 дней, кулдаун 4 дня, проход каждые 6 ч; **карточный ящик, возвраты, молчание кассы и напоминания о переводах — каждые `CARD_TICK_SECONDS=60` с** |
| `order_ref` | чем оплачен Boost: **`ls:<order_id>`** (карта) / **`tg:<telegram_payment_charge_id>`** (Stars) / **`manual:<boost_id>`** (перевод). Возврат ищет строку `boosts` именно по нему — без `order_ref` возврат найти платёж не может |
| Возврат / чарджбек | статусы заказа LS: `pending \| failed \| paid \| refunded \| partial_refund \| fraudulent`. Снимаем Boost и минусуем выручку только при `refunded` и `fraudulent`; `partial_refund` **не** трогает ни Boost, ни выручку. Топ объявления снимается только если возвращённый Boost был последним оплаченным (`latest_boost_for_listing`), иначе остался более поздний платёж |
| Возврат перевода (MIA) | кнопки `manref:` → `manrefyes:` в сообщении админа после `manapp`; `scheduler.refund_manual_claim()` ищет платёж по `payments.telegram_charge_id = 'manual:<boost_id>'`, ставит `refunded_at`, `boosts.status='refunded'` и снимает топ, если Boost был последним. Автоматического отзыва перевода не существует — деньги всегда возвращает человек в своём банке |
| Напоминание о переводе | `MANUAL_REMIND_HOURS = 6` — заявка «я перевёл» без подтверждения админа приходит админу раз в 6 ч; дедупликация живёт в `events` (`manual_remind`, `meta = boost_id`), а не в памяти процесса ⇒ переживает рестарт |
| Истечение Boost | `notify_expired_boosts` предлагает купить Boost заново в окне **48 ч** после `boosted_until`; один раз на объявление (строка `events` с `name='boost_expired'` и `meta = boosted_until`) |
| Бэкап | cron `03:00` ежедневно, `/opt/imobiliare/data/backups/imobiliare-<UTC>.db` + `<...>.db.env` (0600) + `photos-latest.tgz`, ротация **14 дней**, каждый снимок проходит `PRAGMA integrity_check`; лог — `/var/log/imobiliare-backup.log` |
| SSH-защита | fail2ban, jail `sshd`: `maxretry=5`, `findtime=10m`, `bantime=1h`, `backend=systemd`, `ignoreip` = 127.0.0.1/8 + ::1 + адрес администратора; drop-in `/etc/fail2ban/jail.d/imobiliare.conf`; `PermitRootLogin prohibit-password` — **только по явному желанию** (`harden_ssh.sh --disable-root-password`) |
| Репозиторий | `github.com/shevchenkovania-stack/gaseste_casa`, ветка `main`, пуш на каждый этап |
