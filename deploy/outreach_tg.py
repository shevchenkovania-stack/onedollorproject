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

# Three pitches, chosen by what the contact actually is. Same plain register as the emails: no
# marketing words. Identical copy-paste to twenty strangers is the fastest way to a spam report, so
# every message also carries the "why this person" line from the CSV and a rotating greeting.
PITCH = {
    # одиночка-риелтор со своими объектами
    "rieltor": (
        "{hi}\n"
        "Мы скоро запускаем доску объявлений по однушкам в Кишинёве (Telegram).\n"
        "{why}\n"
        "Пришлите один свой объект — фото + район + цена — и мы выложим его сами, телефон ваш. "
        "Комиссию не берём ни с вас, ни с клиента, вам просто будут звонить.\n"
        "Если неинтересно — скажите «нет», больше не напишу.\n"
        "https://t.me/gaseste_casa_bot?start=rieltor"
    ),
    # админ канала/группы: объекты и аудитория уже есть
    "kanal": (
        "{hi}\n"
        "Мы скоро запускаем доску объявлений по однушкам в Кишинёве (Telegram). Размещение "
        "бесплатное, комиссию не берём ни с кого.\n"
        "{why}\n"
        "Не могли бы вы дать нам свои объекты? Мы сами выложим их у себя — с вашими фото и вашим "
        "телефоном, плюс ссылка на ваш канал в каждом объявлении. Ещё одно место, где вас видят, "
        "а не замена вашего канала. Работы вам это не добавляет.\n"
        "Если нет — так и скажите, не буду больше писать.\n"
        "https://t.me/gaseste_casa_bot?start=grupa"
    ),
    # агентство: весь портфель разом
    "agentur": (
        "{hi}\n"
        "Мы скоро запускаем доску объявлений по однушкам и квартирам в Кишинёве (Telegram). "
        "Комиссию не берём ни с собственника, ни с арендатора, контакты в объявлении — ваши.\n"
        "{why}\n"
        "Не могли бы вы предоставить нам свои объекты по аренде? Пришлите фото и описание — мы сами "
        "всё выложим и будем продлевать. Это ещё одно место рекламы, ссылка на ваш сайт остаётся.\n"
        "Если неинтересно — ответьте «нет», по этому контакту больше не напишу.\n"
        "https://t.me/gaseste_casa_bot?start=agentstvo"
    ),
}

# Two openings only: they have to read naturally with a name inserted ("Добрый день, коротко по
# делу, Наталья!" is what a longer list produces, and a garbled greeting is a spam tell).
HI = ["Здравствуйте", "Добрый день"]

FOLLOW_UP = (
    "{hi}\n"
    "Вчера писал про доску однушек. Если есть свободная — просто пришлите фото + район + цену "
    "(можно три фотки в одном сообщении), мы выложим сами, телефон ваш. Ни к чему не обязывает: "
    "не пойдёт — уберём."
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


def show(rec: dict, follow: bool = False, copy: bool = True) -> str:
    """Print one ready message. `copy` is only true for the message you are about to send: a batch
    of three would otherwise leave the LAST text in the clipboard while you are sending the first."""
    text = build(rec, follow)
    print("=" * 68)
    print(f"Кому: {rec['chat']}  ·  {rec.get('org', '')}  ·  тип: {rec.get('kind', '')}")
    print(f"Ссылка: {link(rec['chat'])}")
    print("-" * 68)
    print(text)
    print("-" * 68)
    if copy:
        print(copy_to_clipboard(text))
    print(f"Как отправили:  python outreach_tg.py sent {rec['chat']}")
    return text


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
        for i, rec in enumerate(todo[: MAX_BATCH]):
            show(rec, follow=True, copy=i == 0)
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
    batch = queue[:n]
    for i, rec in enumerate(batch):
        show(rec, copy=i == 0)
    print("=" * 68)
    print(f"показал {len(batch)} · В БУФЕРЕ ТЕКСТ ПЕРВОГО · осталось в очереди {len(queue) - len(batch)}"
          f" · сегодня ещё можно {left - len(batch)}")
    print("Открыли ссылку → Ctrl+V → отправили → python outreach_tg.py sent <имя>. "
          "Следующее сообщение - только после паузы, не подряд.")
    print("Пауза между отправками 3-5 минут. 2-3 новых чата за заход — наш же лимит, не больше.")


if __name__ == "__main__":
    main()
