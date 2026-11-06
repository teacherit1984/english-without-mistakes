cat << 'EOF' > /home/user/bot.py
import os
import re
import io
import json
import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timezone, timedelta
from PIL import Image
import pytesseract
import fitz
from maxapi import Bot, Dispatcher
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.types.attachments.attachment import ButtonsPayload

# ОТКЛЮЧАЕМ ЗАЩИТУ ОТ "БОМБ" В PILLOW
Image.MAX_IMAGE_PIXELS = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("MAX_BOT_TOKEN")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")

USERS_FILE = "/app/users.json" if os.path.exists("/app/users.json") else "/home/user/users.json"
BOOKS_DIR = "/app/books" if os.path.exists("/app/books") else "/home/user/books"

ADMIN_USER_IDS = {6032271, 442430152, 382510789}

HARDCODED_NAMES = {
    6032271: "Александр Иванович",
    442430152: "Кирилл",
    202516127: "Елизавета"
}

bot = Bot(token=TOKEN)
dp = Dispatcher()

chat_locks = {}
stats = {"requests": 0, "ocr_runs": 0, "quiz_played": 0}
admin_states = {}
user_states = {}
user_actions = {}
pending_uploads = {}
dialog_history = {}
quiz_sessions = {}

os.makedirs(BOOKS_DIR, exist_ok=True)
REGISTRY_FILE = os.path.join(BOOKS_DIR, "registry.json")

def get_moscow_time():
    moscow_tz = timezone(timedelta(hours=3))
    return datetime.now(moscow_tz).strftime("%d.%m.%Y %H:%M")

def get_chat_lock(chat_id):
    if chat_id not in chat_locks:
        chat_locks[chat_id] = asyncio.Lock()
    return chat_locks[chat_id]

def load_json(filepath, default=None):
    if default is None: default = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_registry(): return load_json(REGISTRY_FILE)
def save_registry(data): save_json(REGISTRY_FILE, data)
def load_users(): return load_json(USERS_FILE)
def save_users(data): save_json(USERS_FILE, data)

def register_user(user_id, user_name):
    if not user_id: return
    users = load_users()
    uid = str(user_id)
    final_name = HARDCODED_NAMES.get(user_id, user_name if user_name else "")
    if uid not in users:
        users[uid] = {
            "name": final_name,
            "first_seen": get_moscow_time(),
            "quizzes": [],
            "best_score": 0
        }
        save_users(users)
    elif final_name and users[uid].get("name") != final_name:
        users[uid]["name"] = final_name
        save_users(users)

def get_user_name(event):
    uid = get_user_id(event)
    if uid and uid in HARDCODED_NAMES:
        return HARDCODED_NAMES[uid]
    msg = getattr(event, 'message', None)
    if msg:
        sender = getattr(msg, 'sender', None)
        if sender:
            name = getattr(sender, 'first_name', None) or getattr(sender, 'name', None) or getattr(sender, 'username', None)
            if name: return name
        from_user = getattr(msg, 'from_user', None)
        if from_user:
            name = getattr(from_user, 'first_name', None) or getattr(from_user, 'name', None)
            if name: return name
    user = getattr(event, 'user', None)
    if user:
        name = getattr(user, 'first_name', None) or getattr(user, 'name', None)
        if name: return name
    if uid:
        users = load_users()
        if str(uid) in users:
            return users[str(uid)].get("name")
    return None

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

def get_chat_id(event):
    chat = getattr(event, 'chat', None)
    if chat:
        cid = getattr(chat, 'chat_id', None) or getattr(chat, 'id', None)
        if cid: return cid
    msg = getattr(event, 'message', None)
    if msg:
        recipient = getattr(msg, 'recipient', None)
        if recipient:
            cid = getattr(recipient, 'chat_id', None)
            if cid: return cid
    return getattr(event, 'chat_id', None)

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
    if not msg: return None, None, None
    event_str = str(event)
    url_match = re.search(r"url='(https://fd\.oneme\.ru/[^']+)'", event_str)
    if not url_match:
        url_match = re.search(r"url='([^']*getfile[^']*)'", event_str)
    if url_match:
        return url_match.group(1), "book.pdf", None
    attachments = getattr(msg, 'attachments', None)
    if attachments:
        for att in attachments:
            if getattr(att, 'type', None) in ['document', 'file', 'pdf']:
                payload = getattr(att, 'payload', None)
                if payload:
                    url = getattr(payload, 'url', None)
                    if url: return url, getattr(payload, 'filename', 'book.pdf'), None
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
    if row: buttons.append(row)
    buttons.append([CallbackButton(text="📊 Мой прогресс", payload="my_progress")])
    buttons.append([CallbackButton(text="🔐 Вход для админа", payload="admin_login")])
    return ButtonsPayload(buttons=buttons).pack()

async def send_main_menu(chat_id, custom_text="📚  Выберите класс учебника:"):
    await bot.send_message(chat_id=chat_id, text=custom_text, attachments=[get_main_menu_keyboard()])

def get_class_menu_keyboard(class_num):
    buttons = []
    if class_num == "9":
        buttons.append([CallbackButton(text="🎓 Подготовка к ОГЭ (ФИПИ)", payload="fipi_oge")])
    elif class_num == "11":
        buttons.append([CallbackButton(text="🎓 Подготовка к ЕГЭ (ФИПИ)", payload="fipi_ege")])
        
    buttons.append([CallbackButton(text="📖 Объяснить страницу", payload="action_explain")])
    buttons.append([CallbackButton(text="🎮 Пройти квиз", payload="action_quiz")])
    buttons.append([CallbackButton(text="⬅️ Назад к классам", payload="back_to_classes")])
    return ButtonsPayload(buttons=buttons).pack()

def get_admin_keyboard():
    buttons = [
        [CallbackButton(text="🏆 Топ пользователей", payload="admin_stats")],
        [CallbackButton(text="👥 Пользователи", payload="admin_users")],
        [CallbackButton(text="📚 Добавить учебник", payload="admin_add_book")],
        [CallbackButton(text="📋 Список учебников", payload="admin_list_books")],
        [CallbackButton(text="⬅️ Назад в меню", payload="back_to_classes")]
    ]
    return ButtonsPayload(buttons=buttons).pack()

async def send_admin_menu(chat_id):
    await bot.send_message(chat_id=chat_id, text="👑 Панель администратора", attachments=[get_admin_keyboard()])

def get_class_selection_keyboard():
    buttons = [[CallbackButton(text=f"{i} класс", payload=f"save_book_class:{i}")] for i in range(5, 12)]
    buttons.append([CallbackButton(text="❌ Отмена", payload="cancel_upload")])
    return ButtonsPayload(buttons=buttons).pack()

def read_page_smart(file_path: str, page_num: int) -> str:
    try:
        doc = fitz.open(file_path)
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return ""
        page = doc[page_num - 1]
        text = page.get_text("text").strip()
        has_letters = bool(re.search(r'[a-zA-Zа-яА-Я]', text))
        is_good_text = len(text) > 100 and has_letters and (' ' in text)
        if is_good_text:
            doc.close()
            return text
        
        stats["ocr_runs"] += 1
        try:
            # УМЕНЬШАЕМ РАЗРЕШЕНИЕ, чтобы OCR не падал с ошибкой "Overly large image"
            # Matrix(1.0, 1.0) делает картинку размером с оригинал (без увеличения)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), dpi=150)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            
            # Принудительно уменьшаем, если все еще большая
            if img.width > 2500 or img.height > 2500:
                img.thumbnail((2500, 2500))

            ocr_text = pytesseract.image_to_string(img, lang='rus+eng').strip()
            doc.close()
            
            if len(ocr_text) < 50:
                return ""
            return ocr_text
        except Exception as e:
            logger.error(f"❌  [OCR ERROR] {e}")
            doc.close()
            return ""
    except Exception as e:
        logger.error(f"❌  [PDF ERROR] {e}")
        return ""

def parse_pages(user_text: str) -> list:
    numbers = re.findall(r'\d+', user_text)
    return [int(n) for n in numbers]

async def get_ai_response(topic: str, chat_id: int, class_num: str, page_num: int = None) -> str:
    registry = load_registry()
    source_text = ""
    if page_num and class_num in registry:
        target_book = os.path.join(BOOKS_DIR, registry[class_num])
        if os.path.exists(target_book):
            source_text = await asyncio.to_thread(read_page_smart, target_book, page_num)
    
    if not source_text:
        return "❌ Не удалось распознать текст на этой странице. Возможно, это плохое сканирование или картинка низкого качества. Попробуйте другую страницу."

    clean_text = re.sub(r'[^\x00-\x7F]+', ' ', source_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    system_prompt = (
        f"Ты — дружелюбный репетитор по английскому языку. Отвечай простым и понятным языком, как для школьника. "
        f"Перед тобой текст со страницы учебника.\n\n"
        f"=== ТЕКСТ ===\n{clean_text}\n================\n\n"
        f"Твоя задача:\n"
        f"1. Выпиши 8-10 ключевых английских слов из текста с переводом (ЛЕКСИКА).\n"
        f"2. Кратко перескажи суть текста (ОБЪЯСНЕНИЕ).\n"
        f"3. Найди в тексте ЗАДАНИЯ (например, вопросы, пропущенные слова, упражнения) и подробно ОБЪЯСНИ, как их выполнить, и дай правильные ответы (ЗАДАНИЯ).\n\n"
        f"Ответь СТРОГО в формате (без звездочек):\n"
        f"ЛЕКСИКА:\n...\n\n"
        f"ОБЪЯСНЕНИЕ:\n...\n\n"
        f"ЗАДАНИЯ:\n..."
    )
    
    history = dialog_history.get(chat_id, [])
    messages = [{"role": "system", "text": system_prompt}] + history + [{"role": "user", "text": topic}]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                json={
                    "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/latest",
                    "completionOptions": {"stream": False, "temperature": 0.5, "maxTokens": 2000},
                    "messages": messages
                },
                headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=90)
            ) as resp:
                data = await resp.json()
                if "result" not in data: return "⚠️ Ошибка. Попробуйте позже."
                answer = data["result"]["alternatives"][0]["message"]["text"]
                clean_answer = answer.replace("**", "")
                if chat_id not in dialog_history: dialog_history[chat_id] = []
                dialog_history[chat_id].append({"role": "user", "text": topic})
                dialog_history[chat_id].append({"role": "assistant", "text": clean_answer})
                if len(dialog_history[chat_id]) > 10: dialog_history[chat_id] = dialog_history[chat_id][-10:]
                return clean_answer.strip()
    except Exception as e:
        logger.error(f"❌  [GPT ERROR] {e}")
        return "⚠️ Ошибка."

async def generate_quiz(class_num: str, pages: list) -> dict:
    registry = load_registry()
    if class_num not in registry: return None
    target_book = os.path.join(BOOKS_DIR, registry[class_num])
    if not os.path.exists(target_book): return None
    combined_text = ""
    for page_num in pages:
        text = await asyncio.to_thread(read_page_smart, target_book, page_num)
        if text:
            combined_text += f"\n--- Страница {page_num} ---\n{text}\n"
    if not combined_text or len(combined_text) < 50: return None
    clean_text = re.sub(r'[^\x00-\x7F]+', ' ', combined_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()[:1500]
    prompt = (
        f"Составь 3 вопроса с вариантами ответов (A, B, C) по этому английскому тексту. "
        f"Ответь СТРОГО в формате JSON: {{\"questions\": [{{\"q\": \"вопрос\", \"options\": [\"вариант A\", \"вариант B\", \"вариант C\"], \"correct\": 0}}]}}\n"
        f"Где correct — это индекс правильного ответа (0, 1 или 2).\n\n"
        f"=== ТЕКСТ ===\n{clean_text}\n"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                json={
                    "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/latest",
                    "completionOptions": {"stream": False, "temperature": 0.3, "maxTokens": 1000},
                    "messages": [{"role": "user", "text": prompt}]
                },
                headers={"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=90)
            ) as resp:
                data = await resp.json()
                answer = data["result"]["alternatives"][0]["message"]["text"]
                match = re.search(r'\{.*\}', answer, re.DOTALL)
                if match: return json.loads(match.group(0))
    except Exception as e:
        logger.error(f"❌  [QUIZ ERROR] {e}")
    return None

async def download_file(url: str, save_path: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    if len(content) > 100:
                        with open(save_path, 'wb') as f: f.write(content)
                        return True
        return False
    except: return False

async def handle_bot_started(event):
    user_id = get_user_id(event)
    chat_id = get_chat_id(event)
    user_name = get_user_name(event)
    if not chat_id: return
    register_user(user_id, user_name)
    admin_states.pop(chat_id, None)
    user_states.pop(chat_id, None)
    user_actions.pop(chat_id, None)
    dialog_history.pop(chat_id, None)
    quiz_sessions.pop(chat_id, None)
    greeting = f"Здравствуйте, {user_name}!" if user_name else "Здравствуйте!"
    await send_main_menu(chat_id, f"👋 {greeting}\n\nВыберите класс учебника или напишите тему для изучения:")

async def handle_message(event):
    user_id = get_user_id(event)
    chat_id = get_chat_id(event)
    if not chat_id: return
    
    user_text = extract_text_from_event(event)
    if user_text: user_text = user_text.strip()
    text_lower = user_text.lower() if user_text else ""
    user_name = get_user_name(event)
    logger.info(f"\n{'='*40}\n📝  [EVENT] user_id={user_id} | Имя: {user_name} | Текст: '{user_text}'")

    if user_id and user_name:
        register_user(user_id, user_name)

    chat_lock = get_chat_lock(chat_id)
    if chat_lock.locked():
        await bot.send_message(chat_id=chat_id, text="⏳ Подождите, я еще думаю над вашим прошлым запросом...")
        return

    async with chat_lock:
        file_url, file_name, file_size = get_file_info(event)
        if file_url and (user_id in ADMIN_USER_IDS or admin_states.get(chat_id) == "add_book"):
            if admin_states.get(chat_id) == "add_book":
                await bot.send_message(chat_id=chat_id, text="⏳  Скачиваю файл...")
                save_path = os.path.join(BOOKS_DIR, f"temp_{chat_id}_{int(time.time())}.pdf")
                if await download_file(file_url, save_path):
                    pending_uploads[chat_id] = {'file_path': save_path, 'file_name': file_name, 'file_size': file_size}
                    admin_states[chat_id] = "add_book_select_class"
                    await bot.send_message(chat_id=chat_id, text=f"✅  Файл \"{file_name}\" загружен!\n\nТеперь выберите класс:", attachments=[get_class_selection_keyboard()])
                else:
                    await bot.send_message(chat_id=chat_id, text="❌  Ошибка при скачивании файла.")
                return

        if text_lower == '/start':
            admin_states.pop(chat_id, None)
            user_states.pop(chat_id, None)
            user_actions.pop(chat_id, None)
            dialog_history.pop(chat_id, None)
            quiz_sessions.pop(chat_id, None)
            greeting = f"Здравствуйте, {user_name}!" if user_name else "Здравствуйте!"
            await send_main_menu(chat_id, f"👋 {greeting}\n\nВыберите класс учебника или напишите тему для изучения:")
            return

        if user_id in ADMIN_USER_IDS and text_lower in ['/admin', 'admin', '/админ']:
            admin_states[chat_id] = "menu"
            await send_admin_menu(chat_id)
            return

        selected_class = user_states.get(chat_id)
        action = user_actions.get(chat_id)
        pages = parse_pages(user_text) if user_text else []

        if pages and selected_class and action == "quiz":
            if len(pages) > 3:
                await bot.send_message(chat_id=chat_id, text="⚠️ Нельзя выбрать больше 3-х страниц за раз. Попробуйте снова.")
                return
            await bot.send_message(chat_id=chat_id, text=f"🎲 Генерирую вопросы по страницам {pages}...")
            quiz_data = await generate_quiz(selected_class, pages)
            if not quiz_data:
                await bot.send_message(chat_id=chat_id, text="❌ Не удалось создать квиз по этим страницам.")
                return
            quiz_sessions[chat_id] = {
                "questions": quiz_data["questions"],
                "current": 0,
                "score": 0,
                "class": selected_class,
                "pages": pages
            }
            await send_quiz_question(chat_id)
            return

        if pages and selected_class and action == "explain":
            await bot.send_message(chat_id=chat_id, text="⏳  Думаю...")
            try:
                stats["requests"] += 1
                page_num = pages[0]
                answer = await get_ai_response(user_text, chat_id, selected_class, page_num)
                if answer:
                    kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад в меню", payload="back_to_classes")]]).pack()
                    await bot.send_message(chat_id=chat_id, text=answer, attachments=[kb])
            except Exception as e:
                logger.error(f"💥  [ERROR] {e}")
                await bot.send_message(chat_id=chat_id, text="⚠️  Ошибка.")
            return

        if user_text and not selected_class:
            await bot.send_message(chat_id=chat_id, text="⏳  Думаю...")
            try:
                stats["requests"] += 1
                answer = await get_ai_response(user_text, chat_id, "all", None)
                if answer:
                    kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад в меню", payload="back_to_classes")]]).pack()
                    await bot.send_message(chat_id=chat_id, text=answer, attachments=[kb])
            except Exception as e:
                logger.error(f"💥  [ERROR] {e}")
                await bot.send_message(chat_id=chat_id, text="⚠️  Ошибка.")
            return

async def handle_callback(event):
    user_id = get_user_id(event)
    user_name = get_user_name(event)
    recipient = getattr(event.message, 'recipient', None) if hasattr(event, 'message') else None
    chat_id = getattr(recipient, 'chat_id', None) if recipient else get_chat_id(event)

    payload = None
    for attr in ['payload', 'data', 'callback_data']:
        val = getattr(event, attr, None)
        if val: payload = val; break
    if not payload:
        callback = getattr(event, 'callback', None)
        if callback:
            for attr in ['payload', 'data']:
                val = getattr(callback, attr, None)
                if val: payload = val; break
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

    logger.info(f"\n{'='*40}\n🔘  [CALLBACK] user_id={user_id} | chat_id={chat_id} | Payload: '{payload}'")

    if not payload: return

    if user_name and user_id:
        register_user(user_id, user_name)

    if payload == "back_to_admin":
        admin_states[chat_id] = "menu"
        await send_admin_menu(chat_id)
        return

    if payload == "back_to_classes":
        user_states.pop(chat_id, None)
        user_actions.pop(chat_id, None)
        admin_states.pop(chat_id, None)
        await send_main_menu(chat_id)
        return

    if payload == "my_progress":
        users = load_users()
        uid = str(user_id)
        
        if uid in users:
            info = users[uid]
            quizzes = info.get('quizzes', [])
            best = info.get('best_score', 0)
            best_text = f"{best} из 3" if best > 0 else "Пока нет результатов"
            display_name = info.get('name', '')
            if not display_name: display_name = f"Пользователь (ID: {uid})"
            
            msg = f"📊 Ваш прогресс:\n\n👤 Имя: {display_name}\n📅 Первый вход: {info.get('first_seen', 'Неизвестно')}\n🎮 Пройдено квизов: {len(quizzes)}\n🏆 Лучший результат: {best_text}"
            if quizzes:
                msg += "\n\nПоследние квизы:\n"
                for q in quizzes[-3:]:
                    msg += f"• {q.get('date', '')}: {q.get('score', 0)}/3 (класс {q.get('class', '?')})\n"
        else:
            msg = "📊 Пока нет данных. Начните с выбора класса!"
        kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад в меню", payload="back_to_classes")]]).pack()
        await bot.send_message(chat_id=chat_id, text=msg, attachments=[kb])
        return

    if payload == "admin_login":
        admin_states[chat_id] = "menu"
        await send_admin_menu(chat_id)
        return

    if payload == "admin_stats":
        users = load_users()
        if not users:
            msg = "📈 Статистика:\nПользователей: 0"
        else:
            active_users = [u for u in users.values() if u.get('best_score', 0) > 0]
            sorted_users = sorted(active_users, key=lambda x: x.get('best_score', 0), reverse=True)[:5]
            
            if not sorted_users:
                msg = "🏆 Пока никто не прошел ни одного квиза."
            else:
                msg = "🏆 Топ-5 активных учеников:\n\n"
                for i, u in enumerate(sorted_users, 1):
                    name = u.get('name', '')
                    if not name: name = "Без имени"
                    msg += f"{i}. {name} — {u.get('best_score', 0)} из 3\n"
        
        kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад", payload="back_to_admin")]]).pack()
        await bot.send_message(chat_id=chat_id, text=msg, attachments=[kb])
        return
        
    if payload == "admin_users":
        users = load_users()
        if not users: msg = "👥 Пока никто не заходил."
        else:
            msg = "👥 Список пользователей:\n\n"
            for uid, info in sorted(users.items(), key=lambda x: x[1].get('first_seen', '')):
                name = info.get('name', '')
                if not name: name = "Без имени"
                msg += f"• {name} (ID: {uid}) — {info.get('first_seen', '?')}\n"
        kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад", payload="back_to_admin")]]).pack()
        await bot.send_message(chat_id=chat_id, text=msg, attachments=[kb])
        return
        
    if payload == "admin_add_book":
        admin_states[chat_id] = "add_book"
        await bot.send_message(chat_id=chat_id, text="📤  Отправьте PDF файл учебника прямо в этот чат.")
        return
        
    if payload == "admin_list_books":
        registry = load_registry()
        if not registry: msg = "📋 Пусто."
        else:
            msg = "📋 Учебники:\n\n"
            for cls, f in sorted(registry.items()):
                msg += f"📚 {cls} класс: {f}\n"
        kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад", payload="back_to_admin")]]).pack()
        await bot.send_message(chat_id=chat_id, text=msg, attachments=[kb])
        return

    if payload.startswith("select_class:"):
        class_num = payload.split(":")[1]
        user_states[chat_id] = class_num
        dialog_history.pop(chat_id, None)
        await bot.send_message(chat_id=chat_id, text=f"✅  Выбран {class_num} класс.\n\nЧто будем делать?", attachments=[get_class_menu_keyboard(class_num)])
        return

    if payload == "fipi_oge":
        kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад", payload="back_to_classes")]]).pack()
        await bot.send_message(chat_id=chat_id, text="🎓 Подготовка к ОГЭ по английскому языку.\n\nОткрытый банк заданий ФИПИ:\nhttps://oge.fipi.ru/bank/index.php?proj=8BBD5C99F37898B6402964AB11955663", attachments=[kb])
        return

    if payload == "fipi_ege":
        kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад", payload="back_to_classes")]]).pack()
        await bot.send_message(chat_id=chat_id, text="🎓 Подготовка к ЕГЭ по английскому языку.\n\nОткрытый банк заданий ФИПИ:\nhttps://ege.fipi.ru/bank/index.php?proj=4B53A6CB75B0B5E1427E596EB4931A2A", attachments=[kb])
        return

    if payload == "action_explain":
        user_actions[chat_id] = "explain"
        await bot.send_message(chat_id=chat_id, text="Введите номер страницы, и я объясню текст и задания на ней:")
        return

    if payload == "action_quiz":
        user_actions[chat_id] = "quiz"
        await bot.send_message(chat_id=chat_id, text="Введите номер страницы (или несколько через запятую, например: 65, 66, 87):")
        return

    if payload.startswith("no_book:"):
        class_num = payload.split(":")[1]
        await bot.send_message(chat_id=chat_id, text=f"⚠️  Учебник для {class_num} класса пока не добавлен.")
        return

    if payload.startswith("quiz_answer:"):
        if chat_id not in quiz_sessions: return
        session = quiz_sessions[chat_id]
        answer_idx = int(payload.split(":")[1])
        current_q = session["questions"][session["current"]]
        if answer_idx == current_q["correct"]:
            session["score"] += 1
            await bot.send_message(chat_id=chat_id, text="✅ Правильно!")
        else:
            correct_answer = current_q["options"][current_q["correct"]]
            await bot.send_message(chat_id=chat_id, text=f"❌ Неправильно. Правильный ответ: {correct_answer}")
        session["current"] += 1
        if session["current"] >= len(session["questions"]):
            stats["quiz_played"] += 1
            score = session["score"]
            total = len(session["questions"])

            users = load_users()
            uid = str(user_id)
            if uid in users:
                if 'quizzes' not in users[uid]: users[uid]['quizzes'] = []
                users[uid]['quizzes'].append({
                    'date': get_moscow_time(),
                    'score': score,
                    'class': session.get('class', '?'),
                    'pages': session.get('pages', [])
                })
                if score > users[uid].get('best_score', 0):
                    users[uid]['best_score'] = score
                save_users(users)

            kb = ButtonsPayload(buttons=[[CallbackButton(text="⬅️ Назад в меню", payload="back_to_classes")]]).pack()
            await bot.send_message(chat_id=chat_id, text=f"🏁 Квиз завершён!\nВаш результат: {score} из {total}.", attachments=[kb])
            quiz_sessions.pop(chat_id, None)
        else:
            await send_quiz_question(chat_id)
        return

    if payload.startswith("save_book_class:"):
        class_num = payload.split(":")[1]
        if chat_id not in pending_uploads: return
        upload_info = pending_uploads[chat_id]
        new_filename = f"class_{class_num}.pdf"
        new_path = os.path.join(BOOKS_DIR, new_filename)
        try:
            if os.path.exists(new_path): os.remove(new_path)
            os.rename(upload_info['file_path'], new_path)
            registry = load_registry()
            registry[class_num] = new_filename
            save_registry(registry)
            pending_uploads.pop(chat_id, None)
            admin_states.pop(chat_id, None)
            await bot.send_message(chat_id=chat_id, text=f"✅ Учебник для {class_num} добавлен!")
            await send_admin_menu(chat_id)
        except Exception as e:
            await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка: {e}")
        return

    if payload == "cancel_upload":
        if chat_id in pending_uploads:
            if os.path.exists(pending_uploads[chat_id]['file_path']):
                os.remove(pending_uploads[chat_id]['file_path'])
            pending_uploads.pop(chat_id, None)
        admin_states.pop(chat_id, None)
        await bot.send_message(chat_id=chat_id, text="❌ Отменено.")
        await send_admin_menu(chat_id)
        return

async def send_quiz_question(chat_id):
    session = quiz_sessions[chat_id]
    q = session["questions"][session["current"]]
    buttons = [[CallbackButton(text=opt, payload=f"quiz_answer:{i}")] for i, opt in enumerate(q["options"])]
    await bot.send_message(
        chat_id=chat_id,
        text=f"Вопрос {session['current']+1}/{len(session['questions'])}:\n\n{q['q']}",
        attachments=[ButtonsPayload(buttons=buttons).pack()]
    )

dp.message_created.register(handle_message)
dp.message_callback.register(handle_callback)
try: dp.bot_started.register(handle_bot_started)
except: pass

async def main():
    logger.info("🚀  BOT STARTED")
    if not os.path.exists(REGISTRY_FILE): save_registry({})
    registry = load_registry()
    logger.info(f"📚  Загружено учебников: {registry}")
    pdf_files = [f for f in os.listdir(BOOKS_DIR) if f.endswith('.pdf')]
    logger.info(f"📂  PDF файлы в папке: {pdf_files}")
    if 'spotlight_10.pdf' in pdf_files and '10' not in registry:
        registry['10'] = 'spotlight_10.pdf'
        save_registry(registry)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
EOF
