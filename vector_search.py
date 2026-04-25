"""
Vector Search Engine (N3) — 시맨틱 로어 검색.
Gemini Embedding API를 사용한 코사인 유사도 기반 Top-K 로어 검색.
"관련 로어 찾아줘"를 프롬프트 의존 → 코드 강제로 전환.
"""
import logging
import hashlib
import math
from typing import List, Tuple, Optional

logger = logging.getLogger("VectorSearch")


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """두 벡터의 코사인 유사도 계산."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def keyword_fallback(query: str, chunks: list, top_k: int = 5) -> list:
    """API 장애 시 키워드 기반 폴백 검색."""
    query_tokens = set(query.lower().split())
    scored = []
    for chunk in chunks:
        text = chunk if isinstance(chunk, str) else chunk.get("content", "")
        chunk_tokens = set(text.lower().split())
        overlap = len(query_tokens & chunk_tokens)
        if overlap > 0:
            scored.append((chunk, overlap / max(len(query_tokens), 1)))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


class VectorSearchEngine:
    """Gemini Embedding API 기반 시맨틱 검색 엔진."""

    def __init__(self, client, embedding_model: str = "gemini-embedding-2"):
        self.client = client
        self.model = embedding_model
        self._cache = {}  # {chunk_hash: vector}

    def _hash_text(self, text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    async def embed_chunks(self, texts: List[str]) -> List[List[float]]:
        """Gemini Embedding API 배치 호출. 캐시 히트 시 API 스킵."""
        results = []
        to_embed = []
        to_embed_indices = []

        for i, text in enumerate(texts):
            h = self._hash_text(text)
            if h in self._cache:
                results.append(self._cache[h])
            else:
                results.append(None)
                to_embed.append(text)
                to_embed_indices.append(i)

        if to_embed:
            try:
                # Gemini embedding API call
                response = await self.client.aio.models.embed_content(
                    model=self.model,
                    contents=to_embed,
                )
                embeddings = response.embeddings
                for idx, emb in zip(to_embed_indices, embeddings):
                    vec = emb.values if hasattr(emb, 'values') else emb
                    h = self._hash_text(texts[idx])
                    self._cache[h] = vec
                    results[idx] = vec
            except Exception as e:
                logger.warning(f"Embedding API failed: {e}")
                # Fill None slots with zero vectors
                for idx in to_embed_indices:
                    if results[idx] is None:
                        results[idx] = []

        return results

    async def search(
        self,
        query: str,
        chunks: list,
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> List[Tuple[any, float]]:
        """코사인 유사도 기반 Top-K 반환.

        Args:
            query: 검색 쿼리 (최근 히스토리 등)
            chunks: 로어 청크 리스트 (str 또는 {"content": str, ...})
            top_k: 반환할 최대 개수
            min_score: 최소 유사도 임계값

        Returns: [(chunk, score), ...] 유사도 내림차순
        """
        chunk_texts = []
        for c in chunks:
            if isinstance(c, str):
                chunk_texts.append(c)
            elif isinstance(c, dict):
                chunk_texts.append(c.get("content", str(c)))
            else:
                chunk_texts.append(str(c))

        try:
            all_texts = [query] + chunk_texts
            all_vecs = await self.embed_chunks(all_texts)
            query_vec = all_vecs[0]

            if not query_vec:
                logger.warning("Query embedding empty, falling back to keyword")
                return keyword_fallback(query, chunks, top_k)

            scored = []
            for i, chunk in enumerate(chunks):
                chunk_vec = all_vecs[i + 1]
                if chunk_vec:
                    score = cosine_similarity(query_vec, chunk_vec)
                    if score >= min_score:
                        scored.append((chunk, score))

            scored.sort(key=lambda x: -x[1])
            return scored[:top_k]

        except Exception as e:
            logger.warning(f"Vector search failed: {e}, falling back to keyword")
            return keyword_fallback(query, chunks, top_k)

    def clear_cache(self):
        """캐시 초기화."""
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        return len(self._cache)
