#!/bin/bash
# 1) download 23 apartment-interior images from LoremFlickr into test_data/images/
# 2) clear the previous placeholder channel posts + listings
# 3) re-run seed_test_data.py so the channel fills with real photos
set -e
IMG_DIR=/opt/imobiliare/test_data/images
mkdir -p "$IMG_DIR"
cd "$IMG_DIR"

# filenames must match listings.json photos[]
FILES="01_a 01_b 02_a 02_b 02_c 03_a 03_b 03_c 04_a 05_a 05_b 06_a 06_b 07_a 07_b 08_a 08_b 08_c 09_a 09_b 10_a 10_b 10_c"

# 23 distinct search terms so the images don't all look the same
QUERIES=(
  "apartment,interior" "livingroom,sofa" "kitchen,modern"
  "bedroom,simple"     "studio,flat"     "rental,apartment"
  "apartment,window"   "cozy,living"     "interior,small"
  "kitchen,tiny"       "living,room"     "flat,interior"
  "apartment,bed"      "sofa,room"       "room,clean"
  "home,interior"      "modern,apt"      "pied,terre"
  "renovation,room"    "apartment,lamp"  "small,flat"
  "interior,door"      "rental,room"
)

echo "== downloading 23 images =="
i=0
for name in $FILES; do
  if [ -s "$name.jpg" ]; then
    echo "  $name.jpg  already present, skipping"
    i=$((i+1)); continue
  fi
  q="${QUERIES[$i]}"
  lock=$((100 + i * 37))
  url="https://loremflickr.com/800/600/${q}?lock=${lock}"
  # -L follow redirects, --max-time 25 keep us from hanging forever
  if curl -sSL --max-time 25 -o "$name.jpg" "$url" && [ -s "$name.jpg" ]; then
    echo "  $name.jpg  OK   ($q)"
  else
    echo "  $name.jpg  FAILED ($url) - will fall back to PIL placeholder"
    rm -f "$name.jpg"
  fi
  i=$((i+1))
done

ls -la "$IMG_DIR" | head -30
echo
echo "== clearing previous placeholder channel posts + listings =="
cd /opt/imobiliare/bot
/opt/imobiliare/venv/bin/python <<'PY'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db, config
from aiogram import Bot

async def main():
    await db.init_db()
    b = Bot(token=config.BOT_TOKEN)
    # 1. delete old channel posts (message ids 24..33 from the placeholder seed)
    for mid in range(24, 34):
        try:
            await b.delete_message(config.CHANNEL_ID, mid)
            print("  channel msg", mid, "deleted")
        except Exception as e:
            print("  channel msg", mid, "->", type(e).__name__, str(e)[:80])
    # 2. delete old listings 36..45
    for lid in range(36, 46):
        await db._conn.execute("DELETE FROM listings WHERE id = ?", (lid,))
    await db._conn.commit()
    print("listings 36..45 removed")
    await b.session.close()
    await db.close_db()

asyncio.run(main())
PY

echo
echo "== running seed with real photos =="
cd /opt/imobiliare/test_data
/opt/imobiliare/venv/bin/python seed_test_data.py 2>&1 | tail -30
echo
echo "== final stats =="
cd /opt/imobiliare/bot
/opt/imobiliare/venv/bin/python -c "
import asyncio, sys; sys.path.insert(0,'/opt/imobiliare/bot')
import db
async def m():
    await db.init_db()
    print(await db.stats())
    await db.close_db()
asyncio.run(m())
"
