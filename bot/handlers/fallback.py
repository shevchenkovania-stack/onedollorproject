import logging
import time

from aiogram import Bot, F, Router
from aiogram.types import Message

import config
import keyboards

log = logging.getLogger("chiriebot.fallback")

router = Router()

# per-user rate limit (bug #5): plain text in a group must not spam every admin, and one user
# must not be able to flood the support line. keep the last forward time per user id.
_LAST_FWD: dict[int, float] = {}
_MIN_INTERVAL = 3.0


@router.message(F.text, ~F.text.startswith("/"), F.chat.type == "private")
async def forward_to_admins(message: Message, bot: Bot) -> None:
    """A real support line: plain text a user types in a PRIVATE chat that no other router handled
    gets forwarded to the admins, so "write us in this chat" is true instead of a dead @support
    handle. Group text is ignored (bug #5) and each user is throttled. This router is included LAST,
    so the add-listing wizard (whose steps match an active FSM state) takes its own inputs first.
    """
    who = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    now = time.monotonic()
    if now - _LAST_FWD.get(message.from_user.id, 0.0) < _MIN_INTERVAL:
        # throttled: drop silently so a flood cannot reach the admins
        return
    _LAST_FWD[message.from_user.id] = now
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, f"\U0001F4AC Сообщение от {who}:\n{message.text}")
        except Exception:
            pass
    log.info("user message forwarded to %d admin(s) from %s", len(config.ADMIN_IDS), who)
    await message.answer(
        "Передал модератору \U0001F44C Ответим в этом чате в рабочее время.",
        reply_markup=keyboards.back_menu(),
    )
