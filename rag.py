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
        _model = SentenceTransformer('sentence-transformers/LaBSE')
        _index = faiss.read_index(INDEX_FILE)
        with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
            _chunks = json.load(f)

async def search_context(query: str, top_k: int = 3) -> str:
    try:
        _load_db()
        
        query_embedding = _model.encode([query], convert_to_numpy=True)
        distances, indices = _index.search(query_embedding, top_k)
        
        results = []
        for idx in indices[0]:
            if idx < len(_chunks):
                results.append(_chunks[idx])
                
        return "\n\n---\n\n".join(results)
    except Exception as e:
        print(f"Ошибка поиска в RAG: {e}")
        return ""
