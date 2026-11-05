import os
import re
import io
import json
import asyncio
import logging
import aiohttp
import time
from PIL import Image
import pytesseract
import fitz
from maxapi import Bot, Dispatcher
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.types.attachments.attachment import ButtonsPayload

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("MAX_BOT_TOKEN")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")
BOOKS_DIR = "/home/user/books"

ADMIN_USER_IDS = {6032271, 442430152, 382510789}

bot = Bot(token=TOKEN)
dp = Dispatcher()

active_chats = set()
lock = asyncio.Lock()
message_lock = asyncio.Lock()
stats = {"requests": 0, "ocr_runs": 0}
admin_states = {}
user_states = {}
pending_uploads = {}
dialog_history = {}  # {chat_id: [{"role": "user", "text": "..."}, {"role": "assistant", "text": "..."}]}

os.makedirs(BOOKS_DIR, exist_ok=True)
REGISTRY_FILE = os.path.join(BOOKS_DIR, "registry.json")

def load_registry():
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_registry(data):
    with open(REGISTRY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_id(event):
    for attr in ['user_id', 'sender_id', 'from_id']:
        val = getattr(event, attr, None)
        if val is not None:
            try: return int(val)
            except: pass
    user = getattr(event, 'user', None)
    if user:
        for attr in ['user_id', 'id']:
            val = getattr(user, attr, None)
            if val is not None:
                try: return int(val)
                except: pass
    msg = getattr(event, 'message', None)
    if msg:
        sender = getattr(msg, 'sender', None)
        if sender:
            for attr in ['user_id', 'id']:
                val = getattr(sender, attr, None)
                if val is not None:
                    try: return int(val)
                    except: pass
        from_user = getattr(msg, 'from_user', None)
        if from_user:
            for attr in ['user_id', 'id']:
                val = getattr(from_user, attr, None)
                if val is not None:
                    try: return int(val)
                    except: pass
    try:
        event_str = str(event)
        match = re.search(r'user_id[=:]\s*(\d+)', event_str)
        if match:
            return int(match.group(1))
    except: pass
    return None

def extract_text_from_event(event):
    for attr in ['text', 'body', 'content', 'message_text']:
        val = getattr(event, attr, None)
        if val and isinstance(val, str) and val.strip(): return val.strip()
    msg = getattr(event, 'message', None)
    if msg:
        for attr in ['text', 'body', 'content']:
            val = getattr(msg, attr, None)
            if val and isinstance(val, str) and val.strip(): return val.strip()
        body = getattr(msg, 'body', None)
        if body is not None:
            body_str = str(body)
            for pattern in [r"text='([^']*)'", r'text="([^"]*)"', r'"text":\s*"([^"]*)"']:
                m = re.search(pattern, body_str)
                if m and m.group(1).strip(): return m.group(1).strip()
    return None

def get_file_info(event):
    msg = getattr(event, 'message', None)
    if not msg:
        return None, None, None

    event_str = str(event)

    url_match = re.search(r"url='(https://fd\.oneme\.ru/[^']+)'", event_str)
    if not url_match:
        url_match = re.search(r"url='([^']*getfile[^']*)'", event_str)

    if url_match:
        url = url_match.group(1)
        filename_match = re.search(r"filename='([^']+)'", event_str)
        filename = filename_match.group(1) if filename_match else "book.pdf"
        logger.info(f"✅ [FILE] Найдена ссылка: {url[:50]}...")
        return url, filename, None

    attachments = getattr(msg, 'attachments', None)
    if attachments:
        for att in attachments:
            att_type = getattr(att, 'type', None)
            if att_type in ['document', 'file', 'pdf']:
                payload = getattr(att, 'payload', None)
                if payload:
                    url = getattr(payload, 'url', None)
                    filename = getattr(payload, 'filename', None) or "book.pdf"
                    if url:
                        logger.info(f"✅ [FILE] Найдена ссылка: {url[:50]}...")
                        return url, filename, None

    return None, None, None

def get_main_menu_keyboard():
    registry = load_registry()
    buttons = []
    row = []
    for cls in range(5, 12):
        cls_str = str(cls)
        if cls_str in registry:
            row.append(CallbackButton(text=f"📚 {cls} класс", payload=f"select_class:{cls_str}"))
        else:
            row.append(CallbackButton(text=f"📚 {cls} класс (скоро)", payload=f"no_book:{cls_str}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return ButtonsPayload(buttons=buttons).pack()

async def send_main_menu(chat_id, custom_text="📚  Выберите класс учебника:"):
    logger.info(f"🖥️  [UI] Отправка главного меню в чат {chat_id}")
    await bot.send_message(chat_id=chat_id, text=custom_text, attachments=[get_main_menu_keyboard()])

def get_admin_keyboard():
    buttons = [
        [CallbackButton(text="📊 Статистика", payload="admin_stats")],
        [CallbackButton(text="📢 Рассылка", payload="admin_broadcast")],
        [CallbackButton(text="📚 Добавить учебник", payload="admin_add_book")],
        [CallbackButton(text="📋 Список учебников", payload="admin_list_books")],
        [CallbackButton(text="❌ Выйти", payload="admin_exit")]
    ]
    return ButtonsPayload(buttons=buttons).pack()

async def send_admin_menu(chat_id):
    logger.info(f"🖥️  [UI] Отправка меню админа в чат {chat_id}")
    await bot.send_message(chat_id=chat_id, text="\u200B", attachments=[get_admin_keyboard()])

def get_class_selection_keyboard():
    buttons = [[CallbackButton(text=f"{i} класс", payload=f"save_book_class:{i}")] for i in range(5, 12)]
    buttons.append([CallbackButton(text="❌ Отмена", payload="cancel_upload")])
    return ButtonsPayload(buttons=buttons).pack()

def read_page_smart(file_path: str, page_num: int) -> str:
    start_time = time.time()
    try:
        doc = fitz.open(file_path)
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return ""
        page = doc[page_num - 1]
        text = page.get_text("text").strip()
        has_letters = bool(re.search(r'[a-zA-Zа-яА-Я]', text))
        has_spaces = ' ' in text
        is_good_text = len(text) > 100 and has_letters and has_spaces
        if is_good_text:
            logger.info(f"✅  [PDF] Стр {page_num}: Текст найден")
            doc.close()
            return text
        logger.info(f"🔍  [OCR] Стр {page_num}: Запуск OCR...")
        stats["ocr_runs"] += 1
        try:
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            ocr_text = pytesseract.image_to_string(img, lang='rus+eng').strip()
            if len(ocr_text) < 50:
                ocr_text_eng = pytesseract.image_to_string(img, lang='eng').strip()
                if len(ocr_text_eng) > len(ocr_text): ocr_text = ocr_text_eng
            doc.close()
            result = ocr_text if len(ocr_text) > len(text) else text
            if len(result) > 50:
                logger.info(f"✅  [OCR] Стр {page_num}: Распознано {len(result)} символов")
                return result
            return text if len(text) > 0 else ""
        except Exception as e:
            logger.error(f"❌  [OCR ERROR] {e}")
            doc.close()
            return text if len(text) > 0 else ""
    except Exception as e:
        logger.error(f"❌  [PDF ERROR] {e}")
        return ""

async def get_ai_response(topic: str, chat_id: int, class_num: str, page_num: int = None) -> str:
    registry = load_registry()
    source_text = ""

    if page_num and class_num in registry:
        target_book = os.path.join(BOOKS_DIR, registry[class_num])
        if os.path.exists(target_book):
            source_text = await asyncio.to_thread(read_page_smart, target_book, page_num)

    if source_text:
        clean_text = re.sub(r'[^\x00-\x7F]+', ' ', source_text)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        system_prompt = (
            f"Ты — дружелюбный репетитор по английскому языку. Отвечай простым и понятным языком, как для школьника. "
            f"Разбери следующий текст и составь по нему задания.\n\n"
            f"=== ТЕКСТ ===\n{clean_text}\n================\n\n"
        )
    else:
        system_prompt = (
            f"Ты — дружелюбный репетитор по английскому языку. Отвечай простым и понятным языком, как для школьника. "
            f"Тема запроса: {topic}\n\n"
            f"Объясни эту тему так, чтобы было интересно и понятно. Приведи примеры.\n\n"
        )

    history = dialog_history.get(chat_id, [])
    messages = [{"role": "system", "text": system_prompt}] + history + [{"role": "user", "text": topic}]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                json={
                    "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/latest",
                    "completionOptions": {"stream": False, "temperature": 0.6, "maxTokens": 2000},
                    "messages": messages
                },
                headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=90)
            ) as resp:
                data = await resp.json()
                if "result" not in data:
                    logger.error(f"Ошибка API: {data}")
                    return "⚠️ Ошибка. Попробуйте позже."
                answer = data["result"]["alternatives"][0]["message"]["text"]

                clean_answer = answer.replace("**", "")

                if chat_id not in dialog_history:
                    dialog_history[chat_id] = []
                dialog_history[chat_id].append({"role": "user", "text": topic})
                dialog_history[chat_id].append({"role": "assistant", "text": clean_answer})

                if len(dialog_history[chat_id]) > 10:
                    dialog_history[chat_id] = dialog_history[chat_id][-10:]

                return clean_answer.strip()
    except Exception as e:
        logger.error(f"❌  [GPT ERROR] {e}")
        return "⚠️ Ошибка."

async def download_file(url: str, save_path: str) -> bool:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    if len(content) > 100:
                        with open(save_path, 'wb') as f:
                            f.write(content)
                        return True
                    else:
                        logger.error("❌ Файл пустой или слишком маленький")
                        return False
        return False
    except Exception as e:
        logger.error(f"❌  Ошибка скачивания файла: {e}")
        return False

async def handle_message(event):
    async with message_lock:
        user_id = get_user_id(event)
        chat_id = getattr(event.chat, 'chat_id', None) or getattr(event.chat, 'id', None)
        if not chat_id: return
        user_text = extract_text_from_event(event)
        if user_text: user_text = user_text.strip()
        text_lower = user_text.lower() if user_text else ""
        logger.info(f"\n{'='*40}\n📝  [EVENT] user_id={user_id} | Текст: '{user_text}'")

        file_url, file_name, file_size = get_file_info(event)
        if file_url and user_id in ADMIN_USER_IDS:
            current_state = admin_states.get(chat_id)
            if current_state == "add_book":
                logger.info(f"📥  [ADMIN] Получен файл: {file_name} ({file_size} байт)")
                await bot.send_message(chat_id=chat_id, text="⏳  Скачиваю файл...")
                save_path = os.path.join(BOOKS_DIR, f"temp_{chat_id}_{int(time.time())}.pdf")
                if await download_file(file_url, save_path):
                    logger.info(f"✅  Файл сохранен: {save_path}")
                    pending_uploads[chat_id] = {'file_path': save_path, 'file_name': file_name, 'file_size': file_size}
                    admin_states[chat_id] = "add_book_select_class"
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"✅  Файл \"{file_name}\" загружен!\n\nТеперь выберите класс:",
                        attachments=[get_class_selection_keyboard()]
                    )
                else:
                    logger.error(f"❌  Ошибка скачивания файла")
                    await bot.send_message(chat_id=chat_id, text="❌  Ошибка при скачивании файла. Файл пустой или недоступен.")
                return

        if text_lower == '/start':
            admin_states.pop(chat_id, None)
            user_states.pop(chat_id, None)
            dialog_history.pop(chat_id, None)
            await send_main_menu(chat_id, "👋  Привет! Выберите класс:")
            return

        if user_id in ADMIN_USER_IDS and text_lower in ['/admin', 'admin', '/админ']:
            logger.info("👑  [ADMIN] Команда /admin распознана!")
            admin_states[chat_id] = "menu"
            async with lock: active_chats.discard(chat_id)
            await send_admin_menu(chat_id)
            return

        if user_id in ADMIN_USER_IDS and admin_states.get(chat_id) and admin_states.get(chat_id) not in ["add_book", "add_book_select_class"]:
            logger.info(f"⏳  [ADMIN] Вы в меню. Текст проигнорирован.")
            return

        selected_class = user_states.get(chat_id)

        is_number = bool(re.match(r'^\d+$', user_text)) if user_text else False
        if (is_number or user_text) and selected_class:
            if chat_id in active_chats:
                await bot.send_message(chat_id=chat_id, text="⏳  Подождите...")
                return
            async with lock: active_chats.add(chat_id)
            await bot.send_message(chat_id=chat_id, text="⏳  Думаю...")
            try:
                stats["requests"] += 1
                page_num = int(user_text) if is_number else None
                answer = await get_ai_response(user_text, chat_id, selected_class, page_num)
                if answer is None: return
                await bot.send_message(chat_id=chat_id, text=answer)
                await asyncio.sleep(0.5)
                await send_main_menu(chat_id, "✅  Готово! Выберите класс или введите страницу:")
            except Exception as e:
                logger.error(f"💥  [ERROR] {e}")
                await bot.send_message(chat_id=chat_id, text="⚠️  Ошибка.")
            finally:
                async with lock: active_chats.discard(chat_id)
            return

        if user_text and not selected_class and not (user_id in ADMIN_USER_IDS and admin_states.get(chat_id)):
            await bot.send_message(chat_id=chat_id, text="⚠️  Сначала выберите класс (/start)")
            return

async def handle_callback(event):
    async with message_lock:
        user_id = get_user_id(event)
        recipient = getattr(event.message, 'recipient', None) if hasattr(event, 'message') else None
        chat_id = getattr(recipient, 'chat_id', None) if recipient else getattr(event, 'chat_id', None)

        payload = getattr(event, 'payload', None)
        if not payload:
            callback = getattr(event, 'callback', None)
            if callback:
                payload = getattr(callback, 'payload', None)
        if not payload:
            msg = getattr(event, 'message', None)
            if msg:
                body = getattr(msg, 'body', None)
                if body:
                    payload = getattr(body, 'payload', None)
        if not payload:
            payload = str(event)
            match = re.search(r'payload[=:]\s*[\'"]?([a-zA-Z0-9_:]+)', payload)
            if match:
                payload = match.group(1)

        if not payload: return
        logger.info(f"\n{'='*40}\n🔘  [CALLBACK] user_id={user_id} | Payload: '{payload}'")

        if payload.startswith("select_class:"):
            class_num = payload.split(":")[1]
            user_states[chat_id] = class_num
            dialog_history.pop(chat_id, None)
            await bot.send_message(chat_id=chat_id, text=f"✅  Выбран {class_num} класс.\n\nВведите номер страницы для объяснения, либо просто напишите тему, и я объясню её с помощью ИИ:")
            return

        if payload.startswith("no_book:"):
            class_num = payload.split(":")[1]
            await bot.send_message(chat_id=chat_id, text=f"⚠️  Учебник для {class_num} класса пока не добавлен.")
            return

        if user_id not in ADMIN_USER_IDS:
            logger.warning(f"⛔  Пользователь {user_id} не админ")
            return

        if payload == "admin_stats":
            registry = load_registry()
            msg = f"📈  Статистика:\nЗапросов: {stats['requests']}\nOCR: {stats['ocr_runs']}\nКлассов: {len(registry)}"
            await bot.send_message(chat_id=chat_id, text=msg)

        elif payload == "admin_broadcast":
            admin_states[chat_id] = "broadcast"
            await bot.send_message(chat_id=chat_id, text="✍️  Введите текст рассылки:")

        elif payload == "admin_add_book":
            admin_states[chat_id] = "add_book"
            await bot.send_message(chat_id=chat_id, text="📤  Отправьте PDF файл учебника прямо в этот чат.")

        elif payload == "admin_list_books":
            registry = load_registry()
            if not registry:
                msg = "📋  Учебники не загружены."
            else:
                msg = "📋  Загруженные учебники:\n\n"
                for cls, filename in sorted(registry.items()):
                    filepath = os.path.join(BOOKS_DIR, filename)
                    size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                    size_mb = size / (1024 * 1024)
                    msg += f"📚 {cls} класс: {filename} ({size_mb:.1f} МБ)\n"
            await bot.send_message(chat_id=chat_id, text=msg)

        elif payload == "admin_exit":
            admin_states.pop(chat_id, None)
            pending_uploads.pop(chat_id, None)
            await bot.send_message(chat_id=chat_id, text="🔒  Вышли из админки. Напишите /start")

        elif payload.startswith("save_book_class:"):
            class_num = payload.split(":")[1]
            if chat_id not in pending_uploads:
                await bot.send_message(chat_id=chat_id, text="❌  Файл не найден. Начните загрузку заново.")
                admin_states.pop(chat_id, None)
                await send_admin_menu(chat_id)
                return
            upload_info = pending_uploads[chat_id]
            temp_path = upload_info['file_path']
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) < 100:
                await bot.send_message(chat_id=chat_id, text="❌  Файл пустой или не найден на сервере. Попробуйте загрузить снова.")
                admin_states.pop(chat_id, None)
                pending_uploads.pop(chat_id, None)
                await send_admin_menu(chat_id)
                return
            new_filename = f"class_{class_num}.pdf"
            new_path = os.path.join(BOOKS_DIR, new_filename)
            try:
                if os.path.exists(new_path):
                    os.remove(new_path)
                os.rename(temp_path, new_path)
                registry = load_registry()
                registry[class_num] = new_filename
                save_registry(registry)
                logger.info(f"✅  [ADMIN] Учебник для {class_num} класса добавлен: {new_filename}")
                admin_states.pop(chat_id, None)
                pending_uploads.pop(chat_id, None)
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"✅  Учебник для {class_num} класса добавлен!\n\n📚 Файл: {new_filename}\n📊 Размер: {os.path.getsize(new_path) / (1024*1024):.1f} МБ"
                )
                await send_admin_menu(chat_id)
            except Exception as e:
                logger.error(f"❌  Ошибка сохранения файла: {e}")
                await bot.send_message(chat_id=chat_id, text=f"❌  Ошибка: {e}")
                admin_states.pop(chat_id, None)
                pending_uploads.pop(chat_id, None)
                await send_admin_menu(chat_id)

        elif payload == "cancel_upload":
            if chat_id in pending_uploads:
                upload_info = pending_uploads[chat_id]
                if os.path.exists(upload_info['file_path']):
                    os.remove(upload_info['file_path'])
                pending_uploads.pop(chat_id, None)
            admin_states.pop(chat_id, None)
            await bot.send_message(chat_id=chat_id, text="❌  Загрузка отменена.")
            await send_admin_menu(chat_id)

dp.message_created.register(handle_message)
dp.message_callback.register(handle_callback)

async def main():
    logger.info("🚀  BOT STARTED")
    if not os.path.exists(REGISTRY_FILE):
        save_registry({})
    registry = load_registry()
    logger.info(f"📚  Загружено учебников: {registry}")
    pdf_files = [f for f in os.listdir(BOOKS_DIR) if f.endswith('.pdf')]
    logger.info(f"📂  PDF файлы в папке: {pdf_files}")
    if 'spotlight_10.pdf' in pdf_files and '10' not in registry:
        registry['10'] = 'spotlight_10.pdf'
        save_registry(registry)
        logger.info(f"✅  Автоматически зарегистрирован: 10 класс -> spotlight_10.pdf")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
user@ideafix-eng-bez-osh:~$
