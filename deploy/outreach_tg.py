#!/usr/bin/env python3
"""Telegram outreach queue: the script prepares, a human presses send.

Why it is split this way and not automated end to end:
  * a bot cannot DM a stranger at all - sendMessage to someone who never pressed /start returns
    403 "bot can't initiate conversation with a user". This is a platform rule, not a setting;
  * a userbot (your own account driven by code) is forbidden by Telegram's ToS and gets
    spam-limited within days of cold DMs, and the account that dies is the one that administers
    the channel and the Mini App - the loss is the infrastructure, not the campaign.
So the machine does the thinking (who is next, what to say to exactly this person, what text was
already used, who owes a follow-up) and the person does the one action platforms can detect:
sending to a new chat. Cap below is per day, and MAX_BATCH is what you send in one sitting.

Usage:
  python outreach_tg.py next        show the next contact + copy its text to the clipboard
  python outreach_tg.py next 3      show three (one sitting), then send them by hand
  python outreach_tg.py sent Elena  mark as sent (name, org or handle all resolve)
  python outreach_tg.py reply Elena yes|no|objekt
  python outreach_tg.py follow      who is owed the 2-day nudge
  python outreach_tg.py status      counters
"""
import argparse
import csv
import json
import random
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTACTS_CSV = HERE / "outreach_tg.csv"
LEDGER = HERE / "outreach_tg_ledger.jsonl"

MAX_PER_DAY = 6      # new chats per day from a personal account; 2-3 per sitting, spread out
MAX_BATCH = 3        # one sitting
FOLLOW_UP_HOURS = 48 # the agreed nudge: "пришлите фото + район + цену - выложу сам"

# Three different pitches, chosen by what the contact actually is. Identical copy-paste to twenty
# strangers is the single fastest way to a spam report, so every message also carries a line from
# the CSV (why THIS person) and one of several rotating openings.
PITCH = {
    # А1 - a realtor with their own objects: the concierge ask, nothing to sign up for
    "rieltor": (
        "{hi}\n"
        "Иван, Однушка.md — доска однушек Кишинёва в Telegram. Не посредники, комиссию не берём.\n"
        "{why}\n"
        "Проще всего начать так: **пришлите одно фото + район + цену** — я выложу сам за 10 минут, "
        "телефон ваш, клиенты звонят вам. Никуда нажимать не нужно.\n"
        "Первым, кто выложит, — поднятие в топ бесплатно (7 дней первым в поиске).\n"
        "Если объект неактуален — скажите, больше не напишу.\n"
        "https://t.me/gaseste_casa_bot?start=rieltor"
    ),
    # А8 - a channel/group admin: they already own both objects and an audience
    "kanal": (
        "{hi}\n"
        "Иван, делаю Однушка.md — доску однушек Кишинёва в Telegram (бесплатно, без комиссии).\n"
        "{why}\n"
        "Объекты у вас хорошие. Хотите вторую площадку без работы? Давайте я бесплатно "
        "выложу то, что у вас сейчас есть: телефон ваш, ссылка на ваш канал в каждой карточке. "
        "От вас нужно только разрешение (или фото+район+цену).\n"
        "Не понравится — удаляю всё одной кнопкой, никакого риска.\n"
        "https://t.me/gaseste_casa_bot?start=grupa"
    ),
    # А2 - an agency: the whole portfolio at once
    "agentur": (
        "{hi}\n"
        "Иван, Однушка.md — доска однушек и квартир Кишинёва в Telegram. Размещение бесплатное, "
        "комиссию не берём ни с кого, контакты остаются ваши.\n"
        "{why}\n"
        "Могу выложить весь ваш портфель по аренде: пришлите фото + описания — оформление, "
        "модерацию и продление беру на себя. В каждой карточке ссылка на ваш сайт/канал, то есть "
        "это плюс одна площадка, а не замена вашей.\n"
        "Откажете — скажите одно «нет», по этому адресу больше не напишу.\n"
        "https://t.me/gaseste_casa_bot?start=agentstvo"
    ),
}

HI = ["Здравствуйте", "Добрый день", "Здравствуйте, пишу по делу", "Добрый день, коротко по делу"]

FOLLOW_UP = (
    "{hi}\n"
    "Вчера писал про доску однушек. Если у вас сейчас есть свободная — просто пришлите "
    "фото + район + цену (можно три фотки в сообщении), я выложу сам, телефон ваш. "
    "Это ни к чему не обязывает: не пойдёт — удалю."
)


def load_contacts() -> list[dict]:
    with open(CONTACTS_CSV, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("chat") or "").strip()]


def read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def touched_chats() -> set[str]:
    return {str(r.get("chat", "")).lower() for r in read_ledger()}


def sent_today() -> int:
    day = time.strftime("%Y-%m-%d")
    return sum(1 for r in read_ledger() if str(r.get("at", "")).startswith(day))


def record(chat: str, status: str, note: str = "") -> None:
    row = {"chat": chat, "status": status, "note": note,
           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def build(rec: dict, follow: bool = False) -> str:
    kind = (rec.get("kind") or "rieltor").strip().lower()
    tpl = FOLLOW_UP if follow else PITCH.get(kind, PITCH["rieltor"])
    name = (rec.get("name") or "").strip()
    hi = f"{random.choice(HI)}, {name}!" if name else f"{random.choice(HI)}!"
    why = (rec.get("why") or "").strip()
    why = f"{why}\n" if why else ""
    return tpl.format(hi=hi, why=why).strip()


def copy_to_clipboard(text: str) -> str:
    """Windows clip.exe expects UTF-16LE; anything else falls back to the printed block. The point
    is to remove the typing, not to add a dependency - no shell, so nothing is re-parsed."""
    try:
        subprocess.run(["clip.exe"], input=text.encode("utf-16-le"), check=True, shell=False)
        return "текст в буфере обмена → Ctrl+V в чат"
    except Exception:
        return "clip недоступен — копируйте текст ниже руками"


def link(chat: str) -> str:
    c = chat.strip()
    if c.startswith("@"):
        return f"https://t.me/{c[1:]}"
    if c.startswith(("http://", "https://")):
        return c
    return f"контакт по номеру: {c} (добавьте в Telegram, потом напишите)"


def find_contact(key: str, contacts: list[dict]) -> dict | None:
    """Exact handle first, then a unique substring in handle/name/org. Needed because PowerShell eats
    a bare @Elena26206 (splatting operator), so 'sent Elena' has to work as well."""
    k = key.strip().lower().lstrip("@").replace("+", "")
    for c in contacts:
        if c["chat"].strip().lower().lstrip("@").replace("+", "") == k:
            return c
    for c in contacts:
        hay = " ".join([c.get("chat", ""), c.get("name", ""), c.get("org", "")]).lower().replace("+", "")
        if k and k in hay:
            return c
    return None


def show(rec: dict, follow: bool = False) -> None:
    text = build(rec, follow)
    print("=" * 68)
    print(f"Кому: {rec['chat']}  ·  {rec.get('org', '')}  ·  тип: {rec.get('kind', '')}")
    print(f"Ссылка: {link(rec['chat'])}")
    print("-" * 68)
    print(text)
    print("-" * 68)
    print(copy_to_clipboard(text))
    print(f"Как отправили:  python outreach_tg.py sent {rec['chat']}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="next", choices=["next", "sent", "reply", "follow", "status"])
    ap.add_argument("arg1", nargs="?", help="batch size / chat / yes|no|objekt")
    ap.add_argument("arg2", nargs="?", help="answer for reply")
    args = ap.parse_args()

    contacts = load_contacts()
    done = touched_chats()
    queue = [c for c in contacts if c["chat"].strip().lower() not in done]

    if args.cmd == "status":
        rows = read_ledger()
        by = {}
        for r in rows:
            by[r.get("status", "?")] = by.get(r.get("status", "?"), 0) + 1
        print(f"контактов {len(contacts)} · в очереди {len(queue)} · сегодня отправлено {sent_today()} "
              f"из {MAX_PER_DAY} · статусы: {by}")
        return

    if args.cmd == "sent":
        if not args.arg1:
            sys.exit("нужен контакт, например: python outreach_tg.py sent Elena")
        rec = find_contact(args.arg1, contacts)
        if not rec:
            sys.exit(f"не нашёл контакт по «{args.arg1}» — уточните имя, организацию или @handle")
        record(rec["chat"], "sent")
        print(f"{rec['chat']} ({rec.get('org', '')}) записан как отправленный")
        return

    if args.cmd == "reply":
        if not args.arg1 or not args.arg2:
            sys.exit("формат: python outreach_tg.py reply <контакт> yes|no|objekt")
        rec = find_contact(args.arg1, contacts)
        if not rec:
            sys.exit(f"не нашёл контакт по «{args.arg1}»")
        record(rec["chat"], f"reply_{args.arg2}")
        print(f"{rec['chat']}: {args.arg2}")
        return

    if args.cmd == "follow":
        # anyone written to more than FOLLOW_UP_HOURS ago with no answer recorded yet
        cutoff = time.time() - FOLLOW_UP_HOURS * 3600
        seen = {}
        for r in read_ledger():
            t = time.mktime(time.strptime(r["at"], "%Y-%m-%d %H:%M:%S")) if r.get("at") else 0
            if r.get("status", "").startswith("reply_"):
                seen[r["chat"]] = "answered"
            elif t and t < cutoff:
                seen.setdefault(r["chat"], "nudge")
        by_chat = {c["chat"]: c for c in contacts}
        todo = [by_chat[k] for k, v in seen.items() if v == "nudge" and k in by_chat]
        if not todo:
            print(f"фоллоу-апа пока нет (окно {FOLLOW_UP_HOURS} ч ещё не прошло ни по одному адресу)")
            return
        for rec in todo[: MAX_BATCH]:
            show(rec, follow=True)
        print(f"всего ждут фоллоу-апа: {len(todo)}")
        return

    left = MAX_PER_DAY - sent_today()
    if left <= 0:
        print(f"дневной лимит {MAX_PER_DAY} выбран. Завтра продолжим — больше новых чатов в день "
              "личный аккаунт держит плохо, а одинаковый текст на 20 человек режется сразу")
        return
    if not queue:
        print("очередь пуста — все контакты записаны")
        return
    n = min(int(args.arg1 or 1), MAX_BATCH, left, len(queue))
    for rec in queue[:n]:
        show(rec)
    print("=" * 68)
    print(f"показал {n} · осталось в очереди {len(queue) - n} · сегодня ещё можно {left - n}")
    print("Пауза между отправками 3-5 минут. 2-3 новых чата за заход — наш же лимит, не больше.")


if __name__ == "__main__":
    main()
