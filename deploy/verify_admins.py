"""Clean read of the moderation/wiring config on prod - imports config fresh, mutates nothing.

Run: cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python /tmp/verify_admins.py
"""
import sys
sys.path.insert(0, "/opt/imobiliare/bot")
import config

# only presence + a masked preview; never print the real ids or token
print("BOT_TOKEN set:", bool(config.BOT_TOKEN))
print("ADMIN_IDS count:", len(config.ADMIN_IDS))
print("ADMIN_IDS preview:", [str(a)[:3] + "***" for a in config.ADMIN_IDS])
print("CHANNEL_ID set:", bool(config.CHANNEL_ID))
print("WEBAPP_URL set:", bool(config.WEBAPP_URL))
print("BOT_USERNAME set:", bool(config.BOT_USERNAME))
print("MIA MDL amount:", config.BOOST_MDL, "| pay_phone_set:", bool(config.PAY_PHONE))
print("Stars price:", config.BOOST_STARS)
print("CARD enabled:", config.CARD_ENABLED)
