import os
import asyncio
from dotenv import load_dotenv
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, MessageCallback, CallbackButton, ButtonsPayload
from engine import get_ai_explanation

load_dotenv()

BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_state = {}

def clean_text(text: str) -> str:
    return text.replace('**', '').replace('*', '').replace('`', '').replace('#', '').strip()

async def send_menu(message):
    buttons = [
        [CallbackButton(text="Spotlight 10", payload="grade:10")]
    ]
    keyboard = ButtonsPayload(buttons=buttons).pack()
    await message.answer(
        text="👋 Привет! Я твой помощник по английскому.\n\n"
             "Какую страницу или тему из учебника 10 класса объяснить?\n\n"
             "💡 Примеры запросов:\n"
             "• 52 — разбор страницы\n"
             "• Module 3 — о чём текст и новые слова\n"
             "• Present Simple — правило простыми словами",
        attachments=[keyboard]
    )

@dp.message_created()
async def handle_all(event: MessageCreated):
    message = event.message
    if not message or not message.body:
        return

    text = message.body.text.strip() if message.body.text else ""
    user_id = message.sender.user_id if message.sender else None

    if text.lower() == "/start":
        await message.answer("Привет! Я помогу с английским по Spotlight 10.")
        await send_menu(message)
        return

    if text.isdigit() and int(text) == 10:
        if not (user_id and user_id in user_state and user_state[user_id].get('grade')):
            if user_id:
                user_state[user_id] = {'grade': '10'}
            await message.answer("Выбран 10 класс! Напиши страницу или тему.")
            return

    try:
        saved_grade = user_state.get(user_id, {}).get('grade') if user_id else None
        raw_answer = await get_ai_explanation(text, default_grade=saved_grade)
        await message.answer(clean_text(raw_answer))
        await send_menu(message)
    except Exception as e:
        print(f"Ошибка: {e}")
        await message.answer("Произошла ошибка. Попробуй позже.")
        await send_menu(message)

@dp.message_callback()
async def on_grade_selected(event: MessageCallback):
    cb = event.callback.payload
    if cb and cb.startswith("grade:"):
        grade = cb.split(":")[1]
        uid = event.callback.user.user_id if event.callback.user else None
        if uid:
            user_state[uid] = {'grade': grade}
        await event.answer()
        await event.message.answer(f"Выбран {grade} класс! Напиши страницу или тему.")

async def main():
    print("Бот запущен (асинхронный режим)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
