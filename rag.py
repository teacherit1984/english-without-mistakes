import asyncio

async def search_context(query: str, grade: str = None) -> str:
    return f"[RAG] Найден контекст для запроса '{query}' в {grade} классе"
