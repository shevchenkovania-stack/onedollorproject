#!/usr/bin/env python3
"""Cold B2B outreach for the board: one plain-text email per recipient, walked in order.

Why a script and not a mail-merge service: the offer we send is a concrete human promise
("пришлите объекты — я выложу сам, телефон ваш"), it must read like a person wrote it, and every
recipient must be different. A service template would be both less personal and more spammy.

Hard rules baked in here, because a banned mailbox costs more than a lost lead:
  * plain text only - no HTML, no images, no attachments (the top spam trigger for cold mail);
  * one link, tagged (?start=mail) so events.start.meta shows whether this channel works at all;
  * a daily cap (DAILY_CAP) - the queue walks the list one address per tick, never a blast;
  * a ledger (JSONL): an address is never written to twice, and a restart cannot re-send;
  * an opt-out line in every body - a polite "no" is cheap, a "report spam" click is not.

Credentials live in deploy/outreach.env (git-ignored) or in the environment. Nothing else.

Usage:
  python outreach_mailer.py --dry-run            render the whole queue, send nothing
  python outreach_mailer.py --test me@gmail.com  send 3 real samples to yourself, no ledger write
  python outreach_mailer.py --send               send today's batch, respecting the cap
  python outreach_mailer.py --status             who is already written to

An address recorded as "failed" is not retried automatically - a permanently refused mailbox would
otherwise rejoin the queue forever. --status shows the failures, fix or delete the row in the CSV.
"""
import argparse
import csv
import json
import os
import random
import smtplib
import ssl
import sys
import time
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTACTS_CSV = HERE / "outreach_contacts.csv"
ENV_FILE = HERE / "outreach.env"


def ledger_path() -> Path:
    """Resolved after the env file is read, not at import: OUTREACH_LEDGER has to be able to point
    the sent-list at the VPS data dir so a redeploy cannot erase who was already written to."""
    return Path(os.getenv("OUTREACH_LEDGER", str(HERE / "outreach_ledger.jsonl")))


# caps are per day, not per run: the loop is meant to be started once a day, like a habit
DEFAULT_CAP = 12
DEFAULT_DELAY = (45, 120)  # seconds between messages, jittered - a machine burst looks like a bot


def load_env(path: Path) -> None:
    """Tiny KEY=VALUE reader: no dependency on python-dotenv, because this runs on a bare laptop."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def cfg() -> dict:
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "465")),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASS", "").strip(),
        "from_name": os.getenv("MAIL_FROM_NAME", "Иван · Однушка.md"),
        "reply_to": os.getenv("MAIL_REPLY_TO", "").strip(),
        "bot_url": os.getenv("MAIL_BOT_URL", "https://t.me/gaseste_casa_bot?start=mail"),
        "contact": os.getenv("MAIL_CONTACT", "").strip(),
        "cap": int(os.getenv("DAILY_CAP", DEFAULT_CAP)),
        "ssl": os.getenv("SMTP_SSL", "1").strip().lower() in ("1", "true", "yes"),
    }


SUBJECT = {
    "ru": "Бесплатно выложу ваши объекты аренды на доску однушек Кишинёва",
    "ro": "Public gratuit pe tabloul de garsoniere din Chișinău — listările dvs. de chirie",
}

BODY_RU = """{greeting}

Пишу по делу и коротко. Мы делаем Однушка.md — доску однушек и квартир Кишинёва в Telegram.
Не посредники: комиссию не берём ни с собственника, ни с арендатора, телефон на карточке — ваш.

Предложение: давайте я бесплатно выложу ваши текущие объекты аренды к себе на доску. От вас
нужен только список (или разрешение взять фото и описания из того, что уже опубликовано) —
оформление, модерацию и продление беру на себя, работы вам это не добавляет. В каждой карточке
остаётся ссылка на ваш канал/сайт, то есть это вторая площадка, а не замена вашей.

Чем мы отличаемся от ленты в Telegram: поиск по фильтрам (район / комнаты / цена), и объявление
само истекает — доска не превращается в кладбище протухших объектов.

{note}Если неактуально — ответьте одно «нет», по этому адресу больше не напишу.

{bot_url}

{sig}"""

BODY_RO = """{greeting}

Scurt și la obiect. Facem Однушка.md — un tablou gratuit de anunțuri cu garsoniere și apartamente
în Chișinău, direct în Telegram. Nu suntem intermediari: zero comision, iar telefonul de pe fișă
rămâne al dumneavoastră.

Propunerea: haideți să public gratuit pe tabloul nostru listările dvs. actuale de chirie. Avem
nevoie doar de listă (sau de acordul să preiau pozele și descrierile din ce aveți deja publicat) —
formatarea, moderarea și reînnoirea le fac eu, fără încă o sarcină pentru voi. Pe fiecare fișă
rămâne link către canalul/site-ul dvs., deci e o a doua platformă, nu o înlocuire.

Ce ne diferențiază de un feed Telegram: căutarea cu filtre (sector / camere / preț), iar anunțul
expiră singur — tabloul nu ajunge o groapă de oferte vechi.

{note}Dacă nu e de interes, răspundeți doar cu «nu» — nu mai scriu la această adresă.

{bot_url}

{sig}"""

GREETING = {
    "ru": {"with_name": "Здравствуйте, {name}!", "plain": "Здравствуйте!"},
    "ro": {"with_name": "Bună ziua, {name}!", "plain": "Bună ziua!"},
}


def render(rec: dict, c: dict) -> tuple[str, str]:
    """Subject + plain-text body for one recipient. Personalization is the whole point: a
    recognisable 'we looked at you' line is what keeps a cold mail out of the spam folder."""
    lang = (rec.get("lang") or "ru").strip().lower()[:2]
    if lang not in ("ru", "ro"):
        lang = "ru"
    name = (rec.get("contact") or "").strip()
    g = GREETING[lang]
    greeting = g["with_name"].format(name=name) if name else g["plain"]
    note = (rec.get("note") or "").strip()
    note = f"{note}\n\n" if note else ""
    sig = c["contact"] or "Однушка.md · Telegram-доска однушек Кишинёва"
    body = (BODY_RU if lang == "ru" else BODY_RO).format(
        greeting=greeting, note=note, bot_url=c["bot_url"], sig=sig)
    return SUBJECT[lang], body.rstrip() + "\n"


def load_contacts() -> list[dict]:
    if not CONTACTS_CSV.exists():
        sys.exit(f"no contacts file: {CONTACTS_CSV}")
    with open(CONTACTS_CSV, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("email") or "").strip() and not r["email"].startswith("#")]


def read_ledger() -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def written_emails() -> set[str]:
    return {str(e.get("email", "")).lower() for e in read_ledger()}


def sent_today() -> int:
    day = time.strftime("%Y-%m-%d")
    return sum(1 for e in read_ledger() if str(e.get("sent_at", "")).startswith(day))


def record(rec: dict, lang: str, msg_id: str, status: str, error: str = "") -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"email": rec["email"].strip().lower(), "org": rec.get("org", ""), "lang": lang,
           "msg_id": msg_id, "status": status, "error": error,
           "sent_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_message(rec: dict, c: dict, to: str | None = None) -> EmailMessage:
    """`to` overrides the recipient in test mode. It has to be decided here, not by reassigning
    msg["To"] afterwards: EmailMessage refuses a second To header outright (checked)."""
    subject, body = render(rec, c)
    if to:
        subject = f"[TEST → {rec['email']}] {subject}"
    msg = EmailMessage()
    msg["Subject"] = subject
    # A non-ASCII display name must go through Address, not formataddr: assigning the plain string
    # makes the header serializer raise UnicodeEncodeError at the first Cyrillic name (checked).
    domain = c["user"].partition("@")[2]
    msg["From"] = Address(display_name=c["from_name"], addr_spec=c["user"])
    msg["To"] = (to or rec["email"]).strip()
    if c["reply_to"] and c["reply_to"].lower() != c["user"].lower():
        # answers must land in a human's inbox, or the outreach converts into nothing
        msg["Reply-To"] = c["reply_to"]
    msg["Message-ID"] = make_msgid(domain=domain or None)
    # quoted-printable, not the base64 Python would pick for a Cyrillic body: it is what Gmail itself
    # sends, and some spam filters score base64 text/plain worse.
    msg.set_content(body, cte="quoted-printable")
    return msg


def send(recs: list[dict], c: dict, test_to: str | None = None) -> int:
    if not (c["user"] and c["password"]):
        sys.exit("SMTP_USER / SMTP_PASS are empty — fill deploy/outreach.env (see outreach.env.example)")
    done = 0
    with smtplib.SMTP_SSL(c["host"], c["port"], context=ssl.create_default_context()) \
            if c["ssl"] else smtplib.SMTP(c["host"], c["port"]) as s:
        if not c["ssl"]:
            s.starttls(context=ssl.create_default_context())
        s.login(c["user"], c["password"])
        for i, rec in enumerate(recs):
            msg = build_message(rec, c, to=test_to)
            try:
                s.send_message(msg)
            except Exception as e:  # a refused address must not stop the whole batch
                print(f"  FAIL {rec['email']}: {e}")
                if not test_to:
                    record(rec, rec.get("lang", "ru"), "", "failed", str(e)[:180])
                continue
            done += 1
            tag = "test" if test_to else "sent"
            print(f"  {tag} {i + 1}/{len(recs)} → {rec['email']} ({rec.get('org', '')})")
            if not test_to:
                record(rec, rec.get("lang", "ru"), msg["Message-ID"], "sent")
            if not test_to and i + 1 < len(recs):
                # the jitter is anti-bot camouflage for real recipients; a self-test waits for nothing
                time.sleep(random.uniform(*DEFAULT_DELAY))
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true", help="send today's batch")
    ap.add_argument("--test", metavar="EMAIL", help="send the batch to this address for review only")
    ap.add_argument("--dry-run", action="store_true", help="print what would go out")
    ap.add_argument("--status", action="store_true", help="ledger summary")
    ap.add_argument("--limit", type=int, help="override DAILY_CAP for this run")
    args = ap.parse_args()

    # bodies are Romanian + Russian: a Windows console codepage would crash on ș/ț mid-preview
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    load_env(ENV_FILE)
    c = cfg()
    contacts = load_contacts()
    done = written_emails()
    queue = [r for r in contacts if r["email"].strip().lower() not in done]
    cap = args.limit if args.limit else c["cap"]

    if args.status:
        rows = read_ledger()
        ok = [r for r in rows if r.get("status") == "sent"]
        print(f"contacts {len(contacts)} · written {len(ok)} · failed {len(rows) - len(ok)} · "
              f"queued {len(queue)} · today {sent_today()} · cap/day {cap}")
        for r in rows[-10:]:
            print(f"  {r.get('sent_at')}  {r.get('status'):7}  {r.get('email'):32} {r.get('org', '')}")
        return

    if args.dry_run or not (args.send or args.test):
        for rec in queue:
            subject, body = render(rec, c)
            print("=" * 72)
            print(f"TO: {rec['email']}  ({rec.get('org', '')} / {rec.get('contact', '')})")
            print(f"SUBJECT: {subject}\n{body}")
        print("=" * 72)
        print(f"{len(queue)} адрес(ов) в очереди, сегодня отправлено {sent_today()}, лимит {cap}/день")
        if not args.dry_run:
            print("(ничего не отправлено — это preview; для отправки: --send)")
        return

    if args.test:
        # review mode: three samples by default, so you can read the real thing before a stranger does
        n = send(queue[: (args.limit or min(cap, 3))], c, test_to=args.test)
        print(f"готово: {n} писем ушло на {args.test} (в ledger не записаны)")
        return

    left = cap - sent_today()
    if left <= 0:
        print(f"лимит на сегодня исчерпан ({cap}); завтра очередь продолжится сама")
        return
    batch = queue[:left]
    if not batch:
        print("очередь пуста — все адреса уже написаны")
        return
    print(f"отправляю {len(batch)} из {len(queue)} (остаток лимита {left})")
    n = send(batch, c)
    print(f"готово: {n} отправлено, {len(queue) - n} осталось в очереди")


if __name__ == "__main__":
    main()
