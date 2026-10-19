import os
import json
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

DB_DIR = "vector_db"
INDEX_FILE = os.path.join(DB_DIR, "spotlight_10.index")
CHUNKS_FILE = os.path.join(DB_DIR, "chunks.json")

_model = None
_index = None
_chunks = None

def _load_db():
    global _model, _index, _chunks
    if _model is None:
        if not os.path.exists(INDEX_FILE) or not os.path.exists(CHUNKS_FILE):
            print("База знаний не найдена. Запустите indexer.py")
            return
        _model = SentenceTransformer('intfloat/multilingual-e5-large')
        _index = faiss.read_index(INDEX_FILE)
        with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
            _chunks = json.load(f)

async def search_context(query: str, grade: str = "10", top_k: int = 3) -> str:
    try:
        _load_db()
        if _index is None or _chunks is None:
            return ""
        
        query_embedding = _model.encode([query], convert_to_numpy=True)
        distances, indices = _index.search(query_embedding, top_k)
        
        results = []
        found_indices = []
        for idx in indices[0]:
            clean_idx = int(idx.item() if hasattr(idx, 'item') else idx)
            if 0 <= clean_idx < len(_chunks):
                results.append(_chunks[clean_idx])
                found_indices.append(clean_idx)
                
        print(f"[RAG DEBUG] Найдены чанки по индексам: {found_indices}")
        if results:
            print(f"[RAG DEBUG] Первый найденный фрагмент: {results[0][:100]}...")
            
        return "\n\n---\n\n".join(results) if results else ""
    except Exception as e:
        print(f"Ошибка поиска в RAG: {e}")
        return ""
