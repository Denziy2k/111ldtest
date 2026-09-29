import asyncio
import logging
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, WebAppInfo)
from aiohttp import web

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import game

TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")
PORT = int(os.getenv("PORT", "8080"))
CHANNEL, CHANNEL_URL, AUTHOR_URL = "@cachedmemory", "https://t.me/cachedmemory", "https://t.me/denziy2k"
BASE = Path(__file__).parent

WELCOME = (
    "<b>Слово в слово</b> 📝\n\n"
    "Тренажёр словарных слов по русскому языку для 11ЛД: запоминай слово, собирай его из букв, "
    "набирай очки и попадай в рейтинг. 4 уровня сложности, озвучка и словарь с ударениями.\n\n"
    f"Чтобы пользоваться ботом, нужно подписаться на канал {CHANNEL}."
)
OK_TEXT = "Всё готово, ты подписан ✅\nЖми кнопку и тренируйся!"

log = logging.getLogger("bot")
dp = Dispatcher()
SUB = {}


async def subscribed(bot: Bot, uid: int) -> bool:
    c = SUB.get(uid)
    if c and time.time() - c[0] < (45 if c[1] else 5):
        return c[1]
    try:
        m = await bot.get_chat_member(CHANNEL, uid)
        ok = m.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("Не удалось проверить подписку (бот должен быть админом канала): %s", e)
        ok = False
    SUB[uid] = (time.time(), ok)
    return ok


def kb_sub():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check")],
        [InlineKeyboardButton(text="👤 Автор", url=AUTHOR_URL)]])


def kb_open():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть тест", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="👤 Автор", url=AUTHOR_URL)]])


@dp.message(CommandStart())
async def start(msg: Message, bot: Bot):
    if await subscribed(bot, msg.from_user.id):
        await msg.answer(WELCOME + "\n\n" + OK_TEXT, reply_markup=kb_open())
    else:
        await msg.answer(WELCOME, reply_markup=kb_sub())


@dp.callback_query(F.data == "check")
async def check(cb: CallbackQuery, bot: Bot):
    SUB.pop(cb.from_user.id, None)
    if await subscribed(bot, cb.from_user.id):
        await cb.message.answer(OK_TEXT, reply_markup=kb_open())
        await cb.answer()
    else:
        await cb.answer("Подписка не найдена. Подпишись и нажми ещё раз.", show_alert=True)


# ---------- веб-часть: мини-приложение и API ----------
def api(fn):
    async def handler(request: web.Request):
        u = game.verify(request.headers.get("Authorization", "")[4:], TOKEN)
        if not u:
            return web.json_response({"error": "auth"}, status=401)
        if not await subscribed(request.app["bot"], u["id"]):
            return web.json_response({"error": "sub"}, status=403)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return web.json_response(fn(u, body))
    return handler


async def index(request):
    return web.FileResponse(BASE / "webapp" / "index.html", headers={"Cache-Control": "no-cache"})


async def run_web(bot):
    app = web.Application(client_max_size=10_000)
    app["bot"] = bot
    app.router.add_get("/", index)
    for name in ("me", "dictionary", "start", "question", "answer", "top"):
        app.router.add_post(f"/api/{name}", api(getattr(game, name)))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Мини-приложение слушает порт %s", PORT)


async def main():
    logging.basicConfig(level=logging.INFO)
    if not TOKEN or not WEBAPP_URL:
        raise SystemExit("Задай переменные окружения BOT_TOKEN и WEBAPP_URL")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await run_web(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Тест", web_app=WebAppInfo(url=WEBAPP_URL)))
    except Exception as e:
        log.warning("menu button: %s", e)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
