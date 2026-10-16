"""Download the hero photo of each listing in listings.json into images/<name>.

Reads the per-item "img_url" (an accesimobil.md conversion base like
.../conversions/<code>) + "photos"[0] target filename. Tries a few size
suffixes and keeps the biggest one that comes back as a real JPEG. If nothing
downloads the file is simply absent and seed_test_data.py falls back to a
labelled placeholder, so the seed never fails.

Runs on the VPS:
  powershell -File deploy\\run.ps1 deploy\\test_data\\fetch_acces_images.py \
      'echo ok' -RemoteDir /opt/imobiliare/test_data
"""
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(HERE, "listings.json"), encoding="utf-8"))
IMAGES_DIR = os.path.join(HERE, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# accesimobil serves <base>-small.jpg for sure; try bigger variants first.
SUFFIXES = ["-large.jpg", "-medium.jpg", "-card.jpg", ".jpg", "-small.jpg"]


def _is_jpeg(blob: bytes) -> bool:
    return blob[:3] == b"\xff\xd8\xff" or blob[:6] in (b"GIF87a", b"GIF89a") or blob[1:4] == b"PNG"


def _fetch(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://accesimobil.md/"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            if getattr(r, "status", 200) != 200:
                return None
            blob = r.read()
        return blob if _is_jpeg(blob) and len(blob) > 2000 else None
    except Exception:
        return None


def main() -> None:
    ok = miss = 0
    for item in DATA["listings"]:
        base = item.get("img_url")
        name = (item.get("photos") or ["?"])[0]
        out = os.path.join(IMAGES_DIR, name)
        if os.path.isfile(out) and os.path.getsize(out) > 2000:
            print(f"  {name}  already present")
            ok += 1
            continue
        blob = None
        if base:
            for suf in SUFFIXES:
                blob = _fetch(base + suf)
                if blob:
                    print(f"  {name}  OK  ({len(blob)//1024} KB via {suf})")
                    break
        if blob:
            with open(out, "wb") as f:
                f.write(blob)
            ok += 1
        else:
            print(f"  {name}  FAILED (base={base}) -> seed will use a placeholder")
            miss += 1
    print(f"downloaded {ok}, missing {miss}")


if __name__ == "__main__":
    main()
