import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

import config
import db
import scheduler
from handlers import add, admin, boost, browse, fallback, profile, start

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chiriebot")


async def set_commands(bot: Bot) -> None:
    # the descriptions are the bot's storefront: Telegram shows them in the menu and they are the
    # only copy a user reads before deciding to tap, so they name the action, not the router.
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="search", description="Найти жильё в Кишинёве"),
        BotCommand(command="add", description="Подать объявление бесплатно"),
        BotCommand(command="menu", description="Меню"),
        BotCommand(command="stats", description="Статистика (админ)"),
    ])


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set (create .env)")

    await db.init_db()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.include_router(add.router)
    dp.include_router(browse.router)
    dp.include_router(profile.router)
    dp.include_router(boost.router)
    dp.include_router(admin.router)
    # last: only sees plain text that no other router handled (no command, no active FSM state)
    dp.include_router(fallback.router)

    await set_commands(bot)
    me = await bot.get_me()
    log.info("Bot started: @%s", me.username)

    # startup notice for admins, debounced through the DB (note_startup): the FIRST start
    # in an hour is reported, repeated ones stay silent - otherwise a crash loop or a run
    # of deploys buries the admin in "запущен" messages.
    notify, starts = await db.note_startup()
    log.info("Starts in the last hour: %d (admin notified: %s)", starts, notify)
    if notify:
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, "\u2705 Găsește Casa запущен и готов к работе.")
            except Exception:
                pass

    job = None
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        job = asyncio.create_task(scheduler.run(bot))
        await dp.start_polling(bot)
    finally:
        if job is not None:
            job.cancel()
        await db.close_db()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        log.info("Bot stopped: %s", e)
