import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN
from database import init_db
from handlers import user, admin, build, dev, project

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

dp.include_router(admin.router)
dp.include_router(build.router)
dp.include_router(dev.router)
dp.include_router(project.router)
dp.include_router(user.router)


async def main():
    await init_db()
    await project.start_download_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
