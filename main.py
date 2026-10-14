from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import requests
import re
import os
import fitz  # PyMuPDF
from PIL import Image
import pytesseract
import io

app = FastAPI(title="Английский без ошибок")

# --- НАСТРОЙКИ YANDEX GPT ---
YANDEX_API_KEY = ""
FOLDER_ID = "b1g0ho3tlf5cf9sibok4"

# --- НАСТРОЙКИ УЧЕБНИКОВ И OCR ---
BOOKS_DIR = "books"
DEFAULT_BOOK = "spotlight_10.pdf"
pytesseract.pytesseract.tesseract_cmd = r'D:\Tesseract-OCR\tesseract.exe'

# НОВЫЙ, БОЛЕЕ ДЕТАЛЬНЫЙ SYSTEM PROMPT
SYSTEM_PROMPT = """Ты — профессиональный учитель английского языка и персональный ИИ-репетитор «Английский без ошибок».
Твоя цель: провести полноценный урок по материалу, который предоставил ученик.

ПРАВИЛА ОФОРМЛЕНИЯ (СТРОГО):
1. Используй теги <b> для заголовков разделов и ключевых терминов.
2. Разбивай ответ на логические блоки с помощью тегов <p>.
3. Списки оформляй через <ul> и <li>.
4. Тон: вдохновляющий, подробный, но простой (уровень A2-B1).

СТРУКТУРА ТВОЕГО ОТВЕТА (ОБЯЗАТЕЛЬНО СОБЛЮДАТЬ ПОРЯДОК):
1. <b>🎯 О чем эта страница?</b> — Кратко (2-3 предложения) опиши главную тему текста или упражнения.
2. <b> Важные слова и фразы</b> — Выпиши 5-7 самых сложных или важных слов/выражений из текста. Дай их перевод и пример использования.
3. <b>🧠 Грамматика и конструкции</b> — Если на странице есть грамматика, объясни правило простыми словами. Если это просто текст, обрати внимание на интересные речевые обороты.
4. <b>📝 Разбор заданий</b> — Если на странице есть упражнения (questions, tasks), дай к ним подсказки или объясни, что именно нужно сделать в каждом пункте. Не давай готовых ответов "под копирку", а объясняй логику выполнения.
5. <b>❓ Проверь себя</b> — Задай один интересный вопрос по теме страницы, чтобы ученик мог проверить понимание."""


def convert_md_to_html(text: str) -> str:
    """Конвертирует Markdown от ИИ в чистый HTML"""
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'^- (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    text = re.sub(r'(<li>.*?</li>)', r'<ul>\1</ul>', text, flags=re.DOTALL)

    paragraphs = text.split('\n\n')
    html_parts = []
    for p in paragraphs:
        p = p.strip()
        if p and not p.startswith('<'):
            html_parts.append(f'<p>{p}</p>')
        elif p:
            html_parts.append(p)
    return '\n'.join(html_parts)


def get_text_from_pdf(filename: str, page_number: int) -> str | None:
    """Извлекает текст с конкретной страницы PDF (поддерживает сканы через OCR)"""
    file_path = os.path.join(BOOKS_DIR, filename)

    if not os.path.exists(file_path):
        return None

    try:
        doc = fitz.open(file_path)
        if page_number - 1 >= len(doc) or page_number < 1:
            doc.close()
            return None

        page = doc[page_number - 1]
        text = page.get_text()

        # Если текста мало, запускаем OCR
        if len(text.strip()) < 50:
            print(f"[LOG] Текст не найден, запускаю OCR для стр. {page_number}...")
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_data))
            text = pytesseract.image_to_string(image, lang='rus+eng')

        doc.close()

        if len(text.strip()) < 30:
            return None

        return text.strip()

    except Exception as e:
        print(f"Ошибка OCR/PDF: {e}")
        return None


def get_ai_explanation(topic: str) -> str:
    book_file = DEFAULT_BOOK
    page_num = None

    # 1. Определение книги
    if "spotlight" in topic.lower():
        book_file = "spotlight_10.pdf"

    # 2. Умный поиск страницы
    match = re.search(r'(?:страниц[аеу]|стр\.|с\.|page)\s*(\d+)', topic, re.IGNORECASE)

    if not match:
        all_numbers = re.findall(r'(\d+)', topic)
        for num_str in all_numbers:
            num = int(num_str)
            if 12 <= num <= 300:
                page_num = num
                break

    if match:
        page_num = int(match.group(1))

    context_text = ""
    debug_info = ""

    if page_num:
        pdf_content = get_text_from_pdf(book_file, page_num)

        if pdf_content is None:
            return f"<p style='color:red;'>❌ <b>ТЕКСТ НЕ НАЙДЕН.</b><br>Файл: {book_file}, Страница: {page_num}<br>Возможно, это картинка низкого качества.</p>"

        # Отладочный блок (можно убрать, когда все заработает идеально)
        debug_info = f"<div style='background:#f0f0f0; padding:10px; font-size:11px; margin-bottom:15px; border-left: 4px solid #666;'><b> ТЕХНИЧЕСКАЯ ИНФО:</b> Прочитано {len(pdf_content)} символов со страницы {page_num}.</div>"

        context_text = f"""
        === НАЧАЛО МАТЕРИАЛА СО СТРАНИЦЫ {page_num} ===
        {pdf_content}
        === КОНЕЦ МАТЕРИАЛА ===
        """

    # 3. Формируем запрос к ИИ
    user_prompt = f"Запрос ученика: {topic}"

    if context_text:
        user_prompt += f"""

        {context_text}

        ИНСТРУКЦИЯ ДЛЯ УЧИТЕЛЯ:
        Ты видишь выше полный материал со страницы учебника. 
        Твоя задача — провести по нему полноценный урок, следуя структуре из SYSTEM PROMPT.
        НЕ сокращай объяснения. Будь максимально полезен и подробен.
        Если на странице есть диалоги или тексты для чтения — прокомментируй их смысл.
        """
    else:
        user_prompt += "\nОбъясни эту тему по английскому языку максимально подробно."

    try:
        url = f"https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/latest",
            "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": 3000},
            # Увеличил лимит токенов для длинных ответов
            "messages": [
                {"role": "system", "text": SYSTEM_PROMPT},
                {"role": "user", "text": user_prompt}
            ]
        }
        response = requests.post(url, json=payload, headers=headers, timeout=60)  # Увеличил таймаут
        response.raise_for_status()
        raw_text = response.json()["result"]["alternatives"][0]["message"]["text"]

        final_html = convert_md_to_html(raw_text)
        return debug_info + final_html

    except Exception as e:
        return f"<p style='color:#d32f2f;'>Ошибка ИИ: {str(e)}</p>"


@app.get("/", response_class=HTMLResponse)
async def root():
    html_content = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Английский без ошибок</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;color:#1a1a1a;line-height:1.6;padding:20px;max-width:600px;margin:0 auto}
h1{font-size:22px;font-weight:700;margin-bottom:8px;text-align:center;color:#000}
.subtitle{text-align:center;color:#666;font-size:14px;margin-bottom:24px}
label{display:block;font-size:14px;font-weight:500;margin-bottom:8px;color:#333}
textarea{width:100%;height:100px;padding:12px;border:1px solid #ddd;border-radius:8px;font-size:15px;resize:none;outline:none;font-family:inherit;transition:border-color 0.2s}
textarea:focus{border-color:#000}
button{width:100%;padding:14px;background:#000;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;margin-top:16px;transition:background 0.2s}
button:hover{background:#333}
button:disabled{background:#999;cursor:not-allowed}
#result{margin-top:24px;padding:20px;background:#f9f9f9;border-radius:8px;display:none;font-size:15px;line-height:1.7}
#result b{color:#000;font-weight:700}
#result ul{margin:10px 0 10px 20px}
#result li{margin-bottom:6px}
.loading{color:#666;font-style:italic}
</style>
</head>
<body>
<h1>Английский без ошибок</h1>
<p class="subtitle">Персональный ИИ-репетитор по английскому языку</p>
<label for="topic">Введите тему или номер страницы:</label>
<textarea id="topic" placeholder="Например: Грамматика со страницы 16, Present Simple, Артикли..."></textarea>
<button id="btn" onclick="explain()">Объяснить понятным языком</button>
<div id="result"></div>
<script>
async function explain(){
const t=document.getElementById('topic').value.trim();
if(!t){alert('Введите тему');return}
const b=document.getElementById('btn'),r=document.getElementById('result');
b.disabled=true;b.textContent='⏳ ИИ думает...';
r.style.display='block';r.className='loading';r.innerHTML='Генерирую подробное объяснение...';
try{
const res=await fetch('/explain',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:t})});
const d=await res.json();r.className='';r.innerHTML=d.explanation;
}catch(e){r.className='';r.innerHTML='<p style="color:#d32f2f;">Ошибка соединения. Попробуйте позже.</p>'}
finally{b.disabled=false;b.textContent='Объяснить понятным языком'}
}
</script>
</body>
</html>"""
    return html_content


@app.post("/explain")
async def explain_topic(request: Request):
    data = await request.json()
    topic = data.get("topic", "").strip()
    if not topic:
        return {"explanation": "<p>Укажите тему для объяснения.</p>"}
    explanation = get_ai_explanation(topic)
    return {"explanation": explanation}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
