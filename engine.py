import os
import re
import io
import asyncio
import aiohttp
from dotenv import load_dotenv
from rag import search_context

load_dotenv()

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")
BOOKS_DIR = "books"

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_AVAILABLE = os.path.exists(TESSERACT_PATH)


def _read_pdf_sync(file_path: str, page_num: int) -> str:
    try:
        import fitz
        from PIL import Image
        import pytesseract

        doc = fitz.open(file_path)
        if page_num - 1 >= len(doc) or page_num < 1:
            doc.close()
            return ""

        page = doc[page_num - 1]
        text = page.get_text().strip()

        if len(text) < 50 and TESSERACT_AVAILABLE:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img, lang='rus+eng').strip()

        doc.close()
        return text if len(text) >= 30 else ""
    except Exception as e:
        print(f"Ошибка чтения PDF: {e}")
        return ""


async def get_ai_explanation(topic: str, default_grade: str = None) -> str:
    grade = "10"
    book_file = "spotlight_10.pdf"
    page_num = None

    match = re.search(r'(?:страниц[аеу]|стр\.|с\.)\s*(\d+)', topic, re.IGNORECASE)
    if match:
        page_num = int(match.group(1))
    elif re.match(r'^\d+$', topic.strip()):
        num = int(topic.strip())
        if 10 <= num <= 300:
            page_num = num

    text_from_pdf = ""
    rag_context = ""

    if page_num:
        file_path = os.path.join(BOOKS_DIR, book_file)
        if os.path.exists(file_path):
            text_from_pdf = await asyncio.to_thread(_read_pdf_sync, file_path, page_num)

    if not text_from_pdf:
        rag_context = await search_context(topic, grade)

    if page_num and not text_from_pdf and not rag_context:
        return (f"Не могу прочитать страницу {page_num} из {grade} класса. "
                f"Возможно, это скан низкого качества. Попробуй написать тему словами.")

    if text_from_pdf:
        prompt = (f"Материал со стр. {page_num} ({grade} класс):\n{text_from_pdf}\n\n"
                  f"Объясни просто, как ученик. Без вступлений и слов 'нейросеть'.")
    elif rag_context:
        prompt = (f"Контекст из учебника {grade} класса:\n{rag_context}\n\n"
                  f"Запрос: {topic}. Объясни на основе этого текста просто, как ученик.")
    else:
        prompt = (f"Запрос: {topic}. Объясни как ученик {grade} класса. "
                  f"Без вступлений, без 'конечно', без 'ИИ'.")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                    json={
                        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/latest",
                        "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": 1000},
                        "messages": [{"role": "user", "text": prompt}]
                    },
                    headers={
                        "Authorization": f"Api-Key {YANDEX_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                data = await resp.json()
                return data["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        print(f"Ошибка YandexGPT: {e}")
        return "Ошибка соединения с нейросетью. Попробуй позже."
