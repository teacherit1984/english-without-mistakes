from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn
import requests
import re

app = FastAPI(title="Английский без ошибок")

YANDEX_API_KEY = "AQVNxkplrqzodr_tc8NnkscqVg8zCRJwy9ztSRwn"
FOLDER_ID = "b1g0ho3tlf5cf9sibok4"

SYSTEM_PROMPT = """Ты — персональный ИИ-репетитор по английскому языку «Английский без ошибок», встроенный в Сферум.
Твоя задача: объяснять темы из школьных учебников английского языка понятным языком ученику 5-11 класса.

Правила оформления ответа (СТРОГО СОБЛЮДАТЬ):
1. Используй теги <b> для ключевых терминов и заголовков. Никаких звездочек (**)!
2. Разбивай текст на короткие абзацы с помощью тегов <p>.
3. Для примеров используй маркированные списки <ul> и <li>.
4. Тон общения: дружелюбный, поддерживающий, без сложных лингвистических терминов.
5. В конце всегда задавай один короткий проверочный вопрос."""

def convert_md_to_html(text: str) -> str:
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

def get_ai_explanation(topic: str) -> str:
    try:
        url = f"https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/latest",
            "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": 1500},
            "messages": [
                {"role": "system", "text": SYSTEM_PROMPT},
                {"role": "user", "text": f"Объясни тему по английскому языку: {topic}"}
            ]
        }
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        raw_text = response.json()["result"]["alternatives"][0]["message"]["text"]
        return convert_md_to_html(raw_text)
    except Exception as e:
        return f"<p style='color:#d32f2f;'>Ошибка получения ответа от ИИ: {str(e)}. Пожалуйста, попробуйте позже.</p>"

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
<label for="topic">Введите тему из учебника:</label>
<textarea id="topic" placeholder="Например: Present Simple, Артикли, Фразовые глаголы..."></textarea>
<button id="btn" onclick="explain()">Объяснить понятным языком</button>
<div id="result"></div>
<script>
async function explain(){
const t=document.getElementById('topic').value.trim();
if(!t){alert('Введите тему');return}
const b=document.getElementById('btn'),r=document.getElementById('result');
b.disabled=true;b.textContent='⏳ ИИ думает...';
r.style.display='block';r.className='loading';r.innerHTML='Генерирую объяснение...';
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