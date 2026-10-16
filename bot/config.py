import os
from dotenv import load_dotenv

load_dotenv()


def _admins(raw: str):
    out = []
    for x in raw.replace(" ", "").split(","):
        if x.strip().lstrip("-").isdigit():
            out.append(int(x))
    return out


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = _admins(os.getenv("ADMIN_IDS", ""))

DB_PATH = os.getenv("DB_PATH", "/opt/imobiliare/data/imobiliare.db")
PHOTOS_DIR = os.getenv("PHOTOS_DIR", "/opt/imobiliare/data/photos")

DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "EUR")

# Positioning focus: be ONE useful service instead of "yet another board". Empty env keeps the
# full board (any deal, any room count); on prod we lock to one human situation:
# FOCUS_DEAL=rent + FOCUS_ROOMS=1 -> "one-bedroom rentals in Chisinau, from owners".
# This is a display+submission lock, not a data delete: the code path for sale/other rooms stays.
FOCUS_DEAL = os.getenv("FOCUS_DEAL", "").strip()          # "rent" | "sale" | "" (off)
FOCUS_ROOMS = os.getenv("FOCUS_ROOMS", "").strip()        # "1" | "" (off)

# Stars price of one Boost. 400 Stars ~= $5.20 of reward at the documented $0.013/Star, i.e. the
# same money the card rail costs. 50 (the first value we shipped) was ~$0.65: a ten-times hole.
BOOST_STARS = int(os.getenv("BOOST_STARS", "400"))
BOOST_DAYS = int(os.getenv("BOOST_DAYS", "7"))

# Listing life cycle (Stage 4): a live ad is automatically archived after LISTING_DAYS so the
# board shows real supply, not year-old listings. The owner gets a warning EXPIRE_REMINDER_DAYS
# before and can extend with one tap. 0 would mean "never expire" - we keep a real window.
LISTING_DAYS = int(os.getenv("LISTING_DAYS", "30"))
EXPIRE_REMINDER_DAYS = int(os.getenv("EXPIRE_REMINDER_DAYS", "5"))

# Alternative payment rail: instant transfer in MDL, confirmed by a human admin.
# In Moldova this is MIA (Schemele Naționale de Plăți Instant, BNM): a P2P transfer to a phone
# number inside any banking app — no card data entry, no 3-D Secure, no acquiring contract,
# no merchant account. Money arrives in seconds, the buyer confirms it in the bot, we tick it.
# 0 -> disabled.
BOOST_MDL = int(os.getenv("BOOST_MDL", "0"))
# Recipient phone the buyer sends to via MIA. Set -> the bot leads with the MIA flow.
PAY_PHONE = os.getenv("PAY_PHONE", "").strip()
# Name the buyer must see on the confirmation screen of their banking app (trust signal).
PAY_NAME = os.getenv("PAY_NAME", "").strip()
# Any other details, free text: card number / IBAN / bank, if you prefer to receive that way.
PAY_INFO = os.getenv("PAY_INFO", "").strip()

# Rail 3: real card payment (Visa/Mastercard) through Lemon Squeezy as merchant of record.
# Stripe does not issue merchant accounts in Moldova and local acquiring needs a legal entity;
# LS lists Moldova as a supported bank-payout country and handles VAT/chargebacks itself.
LS_API_KEY = os.getenv("LS_API_KEY", "").strip()
LS_STORE_ID = os.getenv("LS_STORE_ID", "").strip()
LS_VARIANT_ID = os.getenv("LS_VARIANT_ID", "").strip()
# Signing secret from LS Settings -> Webhooks; the X-Signature header is HMAC-SHA256 of the raw body.
LS_WEBHOOK_SECRET = os.getenv("LS_WEBHOOK_SECRET", "").strip()
# Price of one Boost in US cents on the LS checkout (store currency). 500 = $5.00 ~ 90 MDL.
CARD_PRICE_CENTS = int(os.getenv("CARD_PRICE_CENTS", "500"))
# Button appears only when the store is actually wired up; webhook needs the secret too.
CARD_ENABLED = bool(LS_API_KEY and LS_STORE_ID and LS_VARIANT_ID)

# Revenue loop: remind owners of unboosted active listings to buy a Boost.
NUDGE_AFTER_DAYS = int(os.getenv("NUDGE_AFTER_DAYS", "2"))
NUDGE_COOLDOWN_DAYS = int(os.getenv("NUDGE_COOLDOWN_DAYS", "4"))
NUDGE_INTERVAL_HOURS = int(os.getenv("NUDGE_INTERVAL_HOURS", "6"))

# Founders gift: the first GIFT_SLOTS users whose listing gets approved receive GIFT_BOOSTS free
# top-raises each. This is the outreach engine - "принеси объект, получи поднятие в топ бесплатно".
# Free boosts never touch the payment rails: no payments rows, revenue stays honest.
# GIFT_BOOSTS=0 disables the program entirely. 1, not 10: a boost lasts 7 days; 10 credits push the
# first real payment ~70 days out. The purpose is to let them try the drug ONCE, then sell the next.
# 10 slots, not 100: the scarcity must be real - "осталось 4 места" через неделю продаёт лучше.
GIFT_BOOSTS = int(os.getenv("GIFT_BOOSTS", "1"))
GIFT_SLOTS = int(os.getenv("GIFT_SLOTS", "10"))

CONTACT_URL = os.getenv("CONTACT_URL", "")

# Public channel where approved listings are auto-posted (@username or -100... chat id)
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip() or None
# t.me link derived from CHANNEL_ID when it is a public @username (empty otherwise)
CHANNEL_URL = f"https://t.me/{CHANNEL_ID[1:]}" if CHANNEL_ID and CHANNEL_ID.startswith("@") else ""
# HTTPS URL of the Telegram Mini App (webapp/). Empty -> app button hidden.
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
# Public username, used for "post your own listing" CTAs in channel posts.
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip()
BOT_URL = f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else ""

# Telegram Stars currency code. Reward value of one Star is $0.013 (Telegram developer terms
# 6.2.4, checked 11.10.2026), so BOOST_STARS must be read as "stars x 0.013 USD", NOT as cents:
# 50 Stars was ~$0.65 of reward: almost eight times cheaper than the $5.00 card rail.
# What the buyer pays to acquire Stars is a different number (app store prices vary by region).
STARS_CURRENCY = "XTR"
