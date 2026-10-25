"""Parse rentix.md one-bedroom RENTALS in Chişinău into a seed-ready listings file.

Why this exists: the cold-start board is empty and needs real supply. rentix.md serves
server-rendered search HTML (no JS, no proxy) with structured fields already in the markup
and JSON-LD. The owner authorized re-parsing public portal listings for a source-attributed
showcase board (same rule change that previously allowed Acces Imobil).

What it does (runs ON THE VPS, not locally):
  1. fetch the 1-cameră rent search pages  https://rentix.md/ro/search/chirie/apartament/1-camera/chisinau?page=N
  2. split each page into per-card blocks and read: url, rooms, area, floor/floors, price, district
  3. download 1..N hero photos per card (webp) and convert them to JPEG in images/rentixNN_k.jpg
     (Telegram send_photo only accepts JPEG/PNG; Pillow here has webp support)
  4. write rentix_listings.json next to this file, shaped exactly like listings.json so
     seed_test_data.py can consume it unchanged:  python seed_test_data.py rentix_listings.json

Honesty rules baked in:
  - every card links back to its real rentix ad via a "Сайт" contact button (contacts.site),
    so a viewer reaches the true listing + contact instead of a scraped private phone;
  - no invented phone, is_demo=0 (these are real live rentals, not fake placeholders);
  - the description states the source ("rentix.md") to avoid any owners-only implication.

This script only produces the file + downloads photos. It never touches the DB or channel —
that is seed_test_data.py's job, so a re-run is cheap and the publish pacing stays in one place.

Usage (from Windows):
  powershell -File deploy\\run.ps1 deploy\\test_data\\parse_rentix.py 'echo ok' -RemoteDir /opt/imobiliare/test_data
  powershell -File deploy\\run.ps1 deploy\\_hc.ps1 'cd /opt/imobiliare/test_data && /opt/imobiliare/venv/bin/python parse_rentix.py'
"""
import io
import json
import os
import re
import urllib.request

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(HERE, "images")
OUT = os.path.join(HERE, "rentix_listings.json")

BASE = "https://rentix.md"
SEARCH = f"{BASE}/ro/search/chirie/apartament/1-camera/chisinau"

TARGET = int(os.environ.get("RENTIX_TARGET", "10"))   # how many one-bedrooms to publish
MAX_PAGES = int(os.environ.get("RENTIX_PAGES", "4"))  # safety cap on pages scanned
PHOTOS_PER = int(os.environ.get("RENTIX_PHOTOS", "3"))
PRICE_MAX = int(os.environ.get("RENTIX_PRICE_MAX", "1600"))  # a sane one-bedroom ceiling (EUR)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# rentix sector names (ro) -> the board's Russian district labels. rentix spells some sectors
# with both 'â' and 'î' variants across pages, so both are listed; unknown names pass through.
_DIST = {
    "centru": "Центр",
    "râșcani": "Рышкановка", "rîșcani": "Рышкановка", "riscani": "Рышкановка",
    "botanica": "Ботаника",
    "buiucani": "Буюканы",
    "ciocana": "Чеканы", "ceucani": "Чеканы",
    "telecentru": "Телечентр",
    "budeni": "Будешты",
    "bacioi": "Бачой", "băcioi": "Бачой",
    "vadul lui vodă": "Ватра", "vadul lui voda": "Ватра",
    "încub": "Încub", "incub": "Încub",
}


def _fetch(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": BASE + "/"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            if getattr(r, "status", 200) != 200:
                return None
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"    fetch failed {url}: {type(e).__name__}: {e}")
        return None


def _to_int(s: str) -> int | None:
    digits = re.sub(r"\D", "", s or "")
    return int(digits) if digits else None


def _district_from_addr(addr: str) -> str:
    """'str. Mitropolit Varlaam 79, Centru, Chișinău' -> 'Центр'."""
    if not addr:
        return "Chișinău"
    parts = [p.strip() for p in addr.split(",")]
    sector = ""
    for p in reversed(parts):
        low = p.lower()
        if low in ("chișinău", "chisinau"):
            continue
        sector = p
        break
    return _DIST.get(sector.lower(), sector or "Chișinău")


def _parse_card(seg: str) -> dict | None:
    m = re.search(r'href="(/ro/chirie/[^"]*?-(\d+))"', seg)
    if not m:
        return None
    slug, ad_id = m.group(1), m.group(2)

    # title spans: '1-camere apartament', '65 m²', 'etaj 5 din 8' (skip 'Încă N fotografii')
    spans = re.findall(r'<span[^>]*>([^<]+)</span>',
                       seg[: seg.find("card-info-price")] if "card-info-price" in seg else seg)
    rooms = area = floor = floors = None
    for s in spans:
        if r := re.search(r"(\d+)-camere", s):
            rooms = int(r.group(1))
        if r := re.search(r"(\d+)\s*m²", s):
            area = int(r.group(1))
        if r := re.search(r"etaj\s*(\d+)\s*din\s*(\d+)", s):
            floor, floors = int(r.group(1)), int(r.group(2))

    pm = re.search(r'card-price-value"[^>]*>([^<]+)<', seg)
    price = _to_int(pm.group(1)) if pm else None
    currency = "MDL" if (pm and ("lei" in pm.group(1).lower() or "MDL" in pm.group(1))) else "EUR"

    am = re.search(r"card-info-item.*?<span[^>]*>([^<]+)</span>", seg, re.S)
    addr = am.group(1).strip() if am else ""

    if not price or not rooms:
        return None
    return {
        "id": ad_id, "slug_url": slug, "rooms": rooms, "area": area,
        "floor": floor, "floors": floors, "price": price, "currency": currency,
        "district": _district_from_addr(addr), "addr": addr,
    }


def _collect_cards() -> list[dict]:
    cards, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        url = SEARCH if page == 1 else f"{SEARCH}?page={page}"
        body = _fetch(url)
        if not body:
            break
        segs = body.split('class="search-card-container')
        got = 0
        for seg in segs[1:]:
            c = _parse_card(seg)
            if not c or c["id"] in seen:
                continue
            seen.add(c["id"])
            cards.append(c)
            got += 1
        print(f"  page {page}: +{got} cards (total {len(cards)})")
        if len(cards) >= TARGET * 2:   # enough candidates incl. later rejections
            break
    return cards


def _download_photos(fid_base: str, idx: int) -> list[str]:
    """Download up to PHOTOS_PER webp slides of this card and save JPEGs images/rentixNN_k.jpg.
    Returns the list of saved filenames (empty -> the seeder falls back to a placeholder)."""
    fids, out = [], []
    for f in fid_base:
        if f not in fids:
            fids.append(f)
        if len(fids) >= PHOTOS_PER:
            break
    for k, fid in enumerate(fids):
        blob = _fetch_bytes(f"{BASE}/storage/permanent/{fid}_860.webp")
        if not blob:
            continue
        name = f"rentix{idx:02d}_{k}.jpg"
        if _webp_to_jpeg(blob, os.path.join(IMAGES_DIR, name)):
            out.append(name)
    return out


def _fetch_bytes(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": BASE + "/"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            blob = r.read()
        return blob if len(blob) > 2000 else None
    except Exception:
        return None


def _webp_to_jpeg(blob: bytes, dest: str) -> bool:
    try:
        im = Image.open(io.BytesIO(blob))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(dest, "JPEG", quality=85)
        return True
    except Exception as e:
        print(f"    webp->jpg failed: {e}")
        return False


def _description(c: dict) -> str:
    lines = [f"{c['district']} · Кишинёв"]
    facts = [f"{c['rooms']}-комн. квартира"]
    if c["area"]:
        facts.append(f"{c['area']} м²")
    if c["floor"]:
        facts.append(f"{c['floor']}/{c['floors']} этаж" if c["floors"] else f"{c['floor']} этаж")
    lines.append(" · ".join(facts))
    if c["addr"]:
        lines.append(c["addr"].replace(", Chișinău", "").replace(", Chişinău", ""))
    lines.append("")
    lines.append("Реальная аренда из rentix.md — контакты и все фото по кнопке «Сайт».")
    return "\n".join(lines).strip()


def main() -> None:
    os.makedirs(IMAGES_DIR, exist_ok=True)
    print(f"scanning rentix one-bedroom rentals, target {TARGET}, cap <= {PRICE_MAX} EUR")
    cards = _collect_cards()
    # keep attainable one-bedrooms only (search is 1-camera already; guard + price cap anyway),
    # cheapest first so the board reads as real value rather than the priciest outliers.
    cards = [c for c in cards if c["rooms"] == 1
             and (c["currency"] != "EUR" or c["price"] <= PRICE_MAX)]
    cards.sort(key=lambda c: c["price"])
    cards = cards[:TARGET]
    print(f"selected {len(cards)} one-bedroom cards")

    listings = []
    for i, c in enumerate(cards, 1):
        body = _fetch(BASE + c["slug_url"]) or ""
        fids = re.findall(r"storage/permanent/(\d+)_\d+\.webp", body)
        photos = _download_photos(fids, i) if fids else []
        ad_url = BASE + c["slug_url"]
        listings.append({
            "slug": f"rentix{c['id']}",
            "deal": "rent", "ptype": "apartment", "rooms": c["rooms"],
            "price": c["price"], "currency": c["currency"],
            "district": c["district"],
            "area": c["area"], "floor": c["floor"], "floors": c["floors"],
            "phone": None,
            "description": _description(c),
            "features": [],
            "contacts": {"site": ad_url},
            "is_demo": 0,
            "photos": photos,
        })
        print(f"  #{i} rentix{c['id']}  {c['price']}{c['currency']}  {c['district']}"
              f"  photos={len(photos)}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"listings": listings}, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {len(listings)} listings -> {OUT}")


if __name__ == "__main__":
    main()
