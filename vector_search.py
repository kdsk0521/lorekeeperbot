"""
Vector Search Engine (N3) — 시맨틱 로어 검색.
Gemini Embedding API를 사용한 코사인 유사도 기반 Top-K 로어 검색.
"관련 로어 찾아줘"를 프롬프트 의존 → 코드 강제로 전환.
"""
import logging
import hashlib
import math
import re
from typing import Any, Dict, List, Tuple, Optional

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


def _default_embedding_model() -> str:
    """임베딩 모델 기본값 = config.VECTOR_EMBEDDING_MODEL (= env VECTOR_EMBEDDING_MODEL).

    [2026-08-17 env 승격] 종전엔 같은 모델 리터럴이 이 파일에 두 벌 박혀 있었다
    (생성자 기본인자 + get_shared_engine 폴백) → 한 자리로 합치고 값의 주인을 config 로 옮겼다.
    [2026-08-18 기본값 제거] 이 파일에 남아 있던 최후 폴백 상수(모델 실명 1줄)도 걷었다 — 모델 이름의
    주인은 .env 단독. 빈 값은 **openai 경로에선 무해**(analysis_backend.embed_content 가 model
    인자를 무시하고 ANALYSIS_OPENAI_EMBED_MODEL 을 쓴다) → 그 경우엔 조용히 "" 를 돌려준다.
    gemini 경로에서 비어 있으면 그건 설정 사고이므로 에러 로그로 시끄럽게 남긴다
    (부팅 시점 방어는 config.validate_model_env()).
    """
    try:
        import config as _cfg
        _m = (getattr(_cfg, "VECTOR_EMBEDDING_MODEL", "") or "").strip()
        if not _m and (getattr(_cfg, "ANALYSIS_BACKEND", "") or "").lower() != "openai":
            logger.error("[VectorSearch] VECTOR_EMBEDDING_MODEL 미설정 — gemini 임베딩 경로에 "
                         "실을 모델 이름이 없습니다(.env 에 VECTOR_EMBEDDING_MODEL 를 채우세요).")
        return _m
    except Exception:
        return ""


class VectorSearchEngine:
    """Gemini Embedding API 기반 시맨틱 검색 엔진."""

    def __init__(self, client, embedding_model: Optional[str] = None):
        self.client = client
        self.model = embedding_model or _default_embedding_model()
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


# =========================================================
# [2026-07-28] 공용 엔진 싱글턴
# =========================================================
# 병: 소비자마다 `VectorSearchEngine(...)`를 새로 만들면 인스턴스 로컬 `_cache`가
#   즉사해 **같은 로어 청크를 소비자 수만큼 반복 임베딩**한다. fermentation은 이미
#   자체 싱글턴(_get_vector_engine, F2 2026-07-18)으로 이 병을 피했지만, 로어 청크를
#   공유하는 소비자들(로어 랭킹·증류 접지)은 각자 새 인스턴스였다.
#   [2026-08-11] fermentation 전용 싱글턴도 이 공용 엔진에 합류 — 아래 트림 정책 밖에서
#   무제한 성장하던 유일한 예외였다. 이제 발효 회상 = get_shared_engine 위임 래퍼.
# 처방: 로어 청크 계열 소비자는 이 공용 엔진을 쓴다 → 청크 임베딩 1회, 이후 쿼리만 과금.
_shared_engine: Optional["VectorSearchEngine"] = None

# 캐시 무한 성장 방지(공유하면 성장 속도가 빨라진다). dict 삽입순 → 오래된 것부터 트림.
_CACHE_MAX = 4000


def get_shared_engine(client, embedding_model: Optional[str] = None) -> "VectorSearchEngine":
    """로어 청크 계열 소비자 공용 엔진. client가 바뀌면 재생성(캐시 무효)."""
    global _shared_engine
    if _shared_engine is None or getattr(_shared_engine, "client", None) is not client:
        if embedding_model is None:
            embedding_model = _default_embedding_model()
        _shared_engine = VectorSearchEngine(client, embedding_model)
        logger.debug("[VectorSearch] shared engine (re)created")
    elif len(_shared_engine._cache) > _CACHE_MAX:
        _drop = len(_shared_engine._cache) - _CACHE_MAX
        for _k in list(_shared_engine._cache.keys())[:_drop]:
            _shared_engine._cache.pop(_k, None)
        logger.debug("[VectorSearch] shared cache trimmed %d", _drop)
    return _shared_engine


# =========================================================
# [2026-08-17] 장면 연관 청크 — 공용 진입점
# =========================================================
# 병: "이 장면에 관련된 로어 청크"를 뽑는 절차가 orchestration 인라인에만 있었다(L1684~).
#   다른 소비자(리더GM 부록 급식 등)가 같은 걸 원하면 게이트·폴백·규율을 통째로 베껴야 했고,
#   베끼는 순간 `get_shared_engine` 규율(캐시 공유)을 놓칠 자리가 하나 더 생긴다.
# 처방: 로직 소유를 검색층으로 올린다 — 랭킹은 검색의 일이지 오케스트레이션의 일이 아니다.
#   orchestration 인라인은 이 함수 호출로 교체(동작 무변경).
# 규율: **항상 get_shared_engine**. 같은 content면 md5 캐시 히트라 추가 소비자의 과금은 쿼리 1건.
def chunk_text(chunk: Any) -> str:
    """청크(str | {"content": ...} | 기타) → 텍스트. engine.search와 같은 추출 규칙."""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict):
        return str(chunk.get("content", "") or "")
    return str(chunk)


async def get_scene_relevant_chunks(
    client,
    channel_id: str,
    query: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    max_chars: int = 0,
    chunks: Optional[list] = None,
    rank_only_if_more_than_top_k: bool = True,
) -> List[Tuple[Any, float]]:
    """장면(쿼리) 연관 로어 청크 Top-K. 반환 = [(chunk, score), ...] 유사도 내림차순.

    Args:
        chunks: None이면 domain_manager.get_lore_chunks(channel_id)에서 조달.
        top_k / min_score: None이면 config.VECTOR_TOP_K / VECTOR_MIN_SCORE.
        max_chars: >0이면 반환 청크 content를 이 길이로 절단. **원본 비파괴** —
            dict는 얕은 복사본을 돌려준다(도메인 로어 훼손 방지).
        rank_only_if_more_than_top_k: True(기본, orchestration 현행 동작)면 풀이
            top_k 이하일 때 랭킹 자체를 생략([] 반환 = 하류가 전량을 그냥 쓴다).
            작은 풀에서도 발췌가 필요한 소비자(리더 부록)는 False.

    실패·재료 없음 = [] (호출부는 '블록 생략'으로 폴백). 예외를 위로 던지지 않는다.
    """
    if not client or not query or not str(query).strip():
        return []
    try:
        import config as _cfg
        if top_k is None:
            top_k = getattr(_cfg, "VECTOR_TOP_K", 10)
        if min_score is None:
            min_score = getattr(_cfg, "VECTOR_MIN_SCORE", 0.2)
        top_k = int(top_k)
        if top_k <= 0:
            return []
        if chunks is None:
            import domain_manager as _dm
            chunks = _dm.get_lore_chunks(channel_id) or []
        if not chunks:
            return []
        if rank_only_if_more_than_top_k and len(chunks) <= top_k:
            return []

        _vs = get_shared_engine(client)
        ranked = await _vs.search(str(query), list(chunks), top_k=top_k, min_score=min_score)
        if not ranked:
            return []
        if max_chars and int(max_chars) > 0:
            _cap = int(max_chars)
            capped = []
            for c, s in ranked:
                if isinstance(c, dict):
                    _t = str(c.get("content", "") or "")
                    if len(_t) > _cap:
                        c = dict(c)
                        c["content"] = _t[:_cap].rstrip()
                elif isinstance(c, str) and len(c) > _cap:
                    c = c[:_cap].rstrip()
                capped.append((c, s))
            ranked = capped
        logger.debug("[VectorSearch] scene chunks: %d/%d (top_k=%s)",
                     len(ranked), len(chunks), top_k)
        return ranked
    except Exception as e:
        logger.debug(f"[VectorSearch] scene chunks unavailable: {e}")
        return []


# =========================================================
# [2026-08-17] 비밀 스크럽 — 청크 소비자 공용 (reader_gm에서 승격)
# =========================================================
# 병: `_scrub_secret_chunks`는 리더GM 안에 살았는데, 같은 청크 풀을 먹는 소비자가
#   1 → 3(리더 부록 · 월드보드 게시물 · 속마음)으로 늘었다. 리더 모듈에 두면 나머지 둘이
#   **리더를 import**하거나(층 역전: 게시판이 독자를 안다) 절차를 베껴야 한다 — 베끼는 순간
#   "게시물이 비밀을 흘렸다"가 조용히 가능해진다.
# 층 소유 근거: 이 함수가 답하는 질문은 "이 텍스트가 비밀에 닿았나"(리더의 관심)가 아니라
#   **"이 청크를 이 소비자에게 검색해 줘도 되나"** = 검색 적격성이다. `get_scene_relevant_chunks`
#   와 같은 층·같은 풀·같은 호출 직전 자리이므로 검색층이 소유한다.
#   domain_manager(두 테이블의 주인)도 후보였으나, 저장/접근층에 퍼지 텍스트 매칭 휴리스틱을
#   심는 건 층 오염이고, `secret_touched`는 리더의 노출 계측(`_apply_reader_exposure`)과도
#   공유되는 **판정기**라 접근자보다 도구 쪽이 맞다.
# 순환 import 없음: 이 모듈의 모듈-레벨 import는 표준 라이브러리뿐이고, domain_manager는
#   함수 안에서 늦게 들여온다(스모크의 원장 몽키패치도 이 늦은 조회 덕에 먹는다).
# 리더는 별칭으로 계속 옛 이름을 쓴다(`reader_gm._scrub_secret_chunks`) — 소유만 옮겼다.


def content_words(text: str) -> set:
    """영문 내용어(4자+) 집합 — `reader_gm._blurb_spoiler_scrub`와 같은 매칭 축."""
    return {w for w in re.findall(r"[a-z]{4,}", str(text).lower())}


def kr_bigrams(text: str) -> set:
    """한글만 남긴 뒤 bigram 집합. 조사·띄어쓰기 흔들림에 둔감한 한국어 겹침 축."""
    s = re.sub(r"[^가-힣]", "", str(text))
    return {s[i:i + 2] for i in range(len(s) - 1)}


def secret_touched(item: Dict[str, Any], truth: str, surface: str) -> bool:
    """{note, quote} 1항목이 이 비밀에 닿았는가(순수 — 스모크 대상).

    두 축: note(영문 해석) ↔ truth/surface 내용어 3개+ 겹침 또는 포함
         / quote(한국어 원문) ↔ truth/surface 한글 bigram 3개+ 겹침.
    ⚠원장 truth/surface는 추출 스키마상 ENGLISH-ONLY(theoria_analyzer L559)라 영문 축이 주(主) —
    한글 축은 원장에 한국어가 들어온 채널을 위한 안전망이지 기본 경로가 아니다.
    임계 3은 `reader_gm._blurb_spoiler_scrub`의 누설 판정과 동일(같은 질문을 반대 방향에서 묻는 것)."""
    note = str(item.get("note", "") or "").lower()
    quote = str(item.get("quote", "") or "")
    for ref in (truth, surface):
        ref = str(ref or "").strip()
        if not ref:
            continue
        rl = ref.lower()
        if rl in note:
            return True
        if len(content_words(rl) & content_words(note)) >= 3:
            return True
        _rb = kr_bigrams(ref)
        if _rb and len(_rb & kr_bigrams(quote)) >= 3:
            return True
    return False


def secret_refs(channel_id: str, tag: str = "Secret") -> Optional[List[Tuple[str, str]]]:
    """이 채널 비밀 원장의 (truth, surface) 목록. **읽기 실패 = None**(≠ 빈 목록).

    빈 목록은 "비밀이 없다"이고 None 은 "모른다"다 — 둘을 같게 다루면 원장을 못 읽은 턴에
    검증되지 않은 재료가 그대로 나간다. 그래서 None 을 받은 소비자는 그 블록을 통째로
    생략한다(안전측: 재료 부재 < 은닉 오염).
    ※ `domain_manager.get_secret_ledger`는 자체 예외를 삼키고 []를 준다 — 이 None 경로는
      그 위의 방어층(원장 접근자 교체·스텁·상위 예외)이지 유일한 판별기가 아니다.

    [2026-08-18 승격] 소비자가 1(속마음 시트) → 2(월드보드 POSTING NPC)로 늘어 여기로 올렸다.
    `secret_touched`/`scrub_secret_chunks`와 같은 층·같은 원장이고, 절차를 베끼면
    "실패=None"이라는 비대칭이 소비자마다 조용히 어긋난다(그 순간 은닉이 샌다).
    turn_mail 은 옛 이름(`_secret_refs`)으로 계속 부른다 — 소유만 옮겼다.
    """
    try:
        import domain_manager
        rows = domain_manager.get_secret_ledger(channel_id) or []
    except Exception as e:
        logger.debug(f"[{tag}] secret ledger unavailable — block omitted: {e}")
        return None
    return [(str(r.get("truth", "") or ""), str(r.get("surface", "") or ""))
            for r in rows if isinstance(r, dict) and r.get("truth")]


def scrub_secret_chunks(channel_id: str, chunks: list, tag: str = "Lore") -> list:
    """[스포일러 가드 — 청크 단위] secret_ledger truth/surface에 닿는 청크 드롭.

    `reader_gm._blurb_spoiler_scrub`(문장 단위)의 청크 판. 매칭은 `secret_touched` 재사용 —
    같은 질문("이 텍스트가 비밀에 닿았나")을 같은 임계(내용어 3 / bigram 3 / 포함)로 묻는다.
    청크 텍스트를 note·quote 두 축에 **동시** 투입: 원장 truth는 ENGLISH-ONLY라 영문 축이 주(主),
    한글 축은 한국어가 들어온 원장을 위한 안전망(secret_touched와 같은 비대칭).

    사유(왜 스크럽이 소비자마다 필수인가) — 소비자별로 새는 방향이 다르다:
      리더  `_apply_reader_exposure`는 리더의 established가 비밀에 닿았는지를 재서
            leak_pressure 가산을 만든다. 비밀을 프롬프트로 직접 먹이면 '산문을 읽어서 안 것'과
            '급식받아 안 것'이 구분되지 않아 계측 자체가 오염된다.
      게시판 산출이 **공개물**이다. 게시물 하나가 비밀을 실으면 그 자리에서 세계에 공표된다
            (게다가 recent_summaries·handle_registry를 타고 다음 턴 재료로 굳는다).
      속마음 유저에게 열리는 창이다. 인물의 속을 통해 비밀이 도착하면 reveal_gate를 우회한다.
    실패=원본 그대로 반환하지 않고 **빈 리스트**(안전측 폴백: 블록 생략).
    """
    try:
        import domain_manager
        rows = domain_manager.get_secret_ledger(channel_id) or []
        refs = [(str(r.get("truth", "") or ""), str(r.get("surface", "") or ""))
                for r in rows if r.get("truth")]
        if not refs:
            return list(chunks)
        kept = []
        for c in chunks:
            _t = c if isinstance(c, str) else str((c or {}).get("content", "") if isinstance(c, dict) else c)
            _item = {"note": _t, "quote": _t}
            if any(secret_touched(_item, _tr, _sf) for _tr, _sf in refs):
                continue
            kept.append(c)
        if len(kept) != len(chunks):
            logger.debug("[%s] secret scrub dropped %d/%d chunks",
                         tag, len(chunks) - len(kept), len(chunks))
        return kept
    except Exception as e:
        logger.debug(f"[{tag}] scrub failed, block suppressed: {e}")
        return []


async def get_scrubbed_scene_chunks(
    client,
    channel_id: str,
    query: str,
    top_k: int,
    max_chars: int,
    tag: str = "Lore",
) -> List[Tuple[Any, float]]:
    """스크럽 → 랭킹, 한 호출. **비밀을 볼 자격이 없는 소비자의 유일한 진입점**.

    ★스크럽이 **랭킹 앞**에 서는 게 요지다: 비밀 청크가 상위를 먹고 나중에 사라지면
      블록이 통째로 비어 버린다(억제가 아니라 결손). 두 절차를 한 함수로 묶는 이유도
      같다 — 순서를 소비자가 다시 고르게 두면 언젠가 반대로 세운다.
    재료 없음 · 전량 스크럽 · top_k<=0 · 엔진 실패 = [] (호출부는 '블록 생략'으로 폴백).
    """
    if not client or top_k is None or int(top_k) <= 0:
        return []
    if not query or not str(query).strip():
        return []
    try:
        import domain_manager
        chunks = domain_manager.get_lore_chunks(channel_id) or []
    except Exception as e:
        logger.debug(f"[{tag}] lore chunks unavailable: {e}")
        return []
    if not chunks:
        return []
    chunks = scrub_secret_chunks(channel_id, chunks, tag=tag)
    if not chunks:
        return []
    return await get_scene_relevant_chunks(
        client, channel_id, str(query),
        top_k=int(top_k),
        max_chars=int(max_chars or 0),
        chunks=chunks,
        rank_only_if_more_than_top_k=False,  # 로어가 작은 채널도 발췌는 받는다
    )


def format_chunk_lines(ranked: List[Tuple[Any, float]]) -> str:
    """랭킹 결과 → 발췌 블록 본문. 빈 결과·전량 공백 = ""(호출부는 블록 자체를 생략).

    형태는 셋이 같다(`— 라벨\\n본문`, 항목 사이 빈 줄). 소비자마다 다르게 꾸미면
    라벨을 흘리는 자리·안 흘리는 자리가 갈리고, 그게 드리프트의 첫 칸이다.
    점수는 싣지 않는다 — 모델에게 숫자를 주면 순위를 논거로 쓴다.
    """
    lines = []
    for c, _s in (ranked or []):
        _text = chunk_text(c).strip()
        if not _text:
            continue
        _label = str(c.get("label", "") or "").strip() if isinstance(c, dict) else ""
        lines.append(f"— {_label}\n{_text}" if _label else f"— {_text}")
    return "\n\n".join(lines)
