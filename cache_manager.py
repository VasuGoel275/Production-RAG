import os
import redis
import json
from typing import Optional, List

REDIS_URL = os.getenv("REDIS_URL")
_redis_client: Optional[redis.Redis] = None

def get_redis_client() -> Optional[redis.Redis]:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
        
    if REDIS_URL:
        # If it is SSL (rediss://) we can set ssl_cert_reqs to None for Upstash
        if REDIS_URL.startswith("rediss://"):
            _redis_client = redis.Redis.from_url(REDIS_URL, ssl_cert_reqs=None)
        else:
            _redis_client = redis.Redis.from_url(REDIS_URL)
        return _redis_client
    return None

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Calculates cosine similarity between two numeric lists."""
    dot_prod = sum(a * b for a, b in zip(v1, v2))
    norm_a = sum(a * a for a in v1) ** 0.5
    norm_b = sum(b * b for b in v2) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_prod / (norm_a * norm_b)

def get_semantic_cache(session_id: str, query_embedding: List[float], threshold: float = 0.94) -> Optional[str]:
    """Retrieves cached answer if any prior query in the session has cosine similarity >= threshold."""
    client = get_redis_client()
    if not client:
        return None
    try:
        cache_key = f"cache:sem:{session_id}"
        data = client.get(cache_key)
        if not data:
            return None
            
        cached_items = json.loads(data.decode("utf-8"))
        best_match = None
        best_score = -1.0
        
        for item in cached_items:
            cached_emb = item.get("embedding")
            if cached_emb:
                score = cosine_similarity(query_embedding, cached_emb)
                if score > best_score:
                    best_score = score
                    best_match = item
                    
        if best_score >= threshold and best_match:
            print(f"--- Semantic Cache Hit! Cosine Similarity: {best_score:.4f} (Threshold: {threshold}) ---")
            return best_match["answer"]
    except Exception as e:
        print(f"Semantic cache read error: {e}")
    return None

def set_semantic_cache(session_id: str, query: str, query_embedding: List[float], answer: str, expire_seconds: int = 3600):
    """Caches query text, embedding, and generated answer inside the session's semantic cache list."""
    client = get_redis_client()
    if not client:
        return
    try:
        cache_key = f"cache:sem:{session_id}"
        data = client.get(cache_key)
        cached_items = []
        if data:
            cached_items = json.loads(data.decode("utf-8"))
            
        # Append new semantic item
        cached_items.append({
            "query": query,
            "embedding": query_embedding,
            "answer": answer
        })
        
        # Keep only the last 30 items to prevent large payload downloads
        cached_items = cached_items[-30:]
        
        client.setex(cache_key, expire_seconds, json.dumps(cached_items))
    except Exception as e:
        print(f"Semantic cache write error: {e}")

def get_cached_response(session_id: str, query: str) -> Optional[str]:
    """Retrieves cached chatbot response for a session + query key."""
    client = get_redis_client()
    if not client:
        return None
    try:
        cache_key = f"cache:{session_id}:{query.strip().lower()}"
        cached = client.get(cache_key)
        if cached:
            return cached.decode("utf-8")
    except Exception as e:
        print(f"Redis cache read error: {e}")
    return None

def set_cached_response(session_id: str, query: str, response: str, expire_seconds: int = 3600):
    """Caches chatbot response for a session + query key."""
    client = get_redis_client()
    if not client:
        return
    try:
        cache_key = f"cache:{session_id}:{query.strip().lower()}"
        client.setex(cache_key, expire_seconds, response)
    except Exception as e:
        print(f"Redis cache write error: {e}")
