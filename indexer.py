import os
import json
import fitz
import faiss
import numpy as np
import io
from PIL import Image
import pytesseract
from sentence_transformers import SentenceTransformer

BOOK_PATH = "books/spotlight_10.pdf"
DB_DIR = "vector_db"
CHUNK_SIZE = 800
OVERLAP = 100
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def extract_text_from_pdf(path):
    doc = fitz.open(path)
    full_text = ""
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text().strip()
        
        if len(text) < 50:
            try:
                pix = page.get_pixmap(dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(img, lang='rus+eng').strip()
                print(f"Страница {page_num + 1}: распознано через OCR ({len(text)} симв.)")
            except Exception as e:
                print(f"Страница {page_num + 1}: ошибка OCR - {e}")
                text = f"[PAGE {page_num + 1}] Пустая страница или ошибка распознавания"
        else:
            print(f"Страница {page_num + 1}: текст извлечен ({len(text)} симв.)")
            
        full_text += f"\n{text}\n"
        
    doc.close()
    return full_text

def split_text_into_chunks(text, size=CHUNK_SIZE, overlap=OVERLAP):
    words = text.split()
    chunks = []
    current_chunk = []
    
    for word in words:
        current_chunk.append(word)
        if len(" ".join(current_chunk)) >= size:
            chunks.append(" ".join(current_chunk))
            current_chunk = current_chunk[-overlap:]
            
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

def create_vector_db():
    print(f"Читаем {BOOK_PATH}...")
    if not os.path.exists(BOOK_PATH):
        print(f"ОШИБКА: Файл {BOOK_PATH} не найден!")
        return

    text = extract_text_from_pdf(BOOK_PATH)
    
    if len(text.strip()) < 100:
        print("ВНИМАНИЕ: Извлечено очень мало текста. Возможно, PDF состоит из картинок.")
        
    chunks = split_text_into_chunks(text)
    print(f"Разбито на {len(chunks)} фрагментов.")

    print("Создаем эмбеддинги...")
    model = SentenceTransformer('intfloat/multilingual-e5-large')
    embeddings = model.encode(chunks, convert_to_numpy=True, show_progress_bar=True)

    print("Сохраняем базу данных...")
    os.makedirs(DB_DIR, exist_ok=True)
    
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings)
    faiss.write_index(index, os.path.join(DB_DIR, "spotlight_10.index"))

    with open(os.path.join(DB_DIR, "chunks.json"), "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)

    print(f"Готово! База сохранена в папке {DB_DIR}")

if __name__ == "__main__":
    create_vector_db()
