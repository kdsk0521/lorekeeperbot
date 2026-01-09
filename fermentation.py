"""
=========================================================
   FERMENTATION SYSTEM (발효 시스템)
   RisuAI SupaMemory/HypaMemory 스타일 장기 기억 관리
=========================================================

메모리 계층:
  - FRESH: 최근 대화 원본 (최대 40개)
  - FERMENTED: 압축된 중기 기억 (요약 리스트)
  - DEEP: 초압축 장기 기억 (요약의 요약)

흐름:
  FRESH (40개 초과) → compress → FERMENTED
  FERMENTED (5개 초과) → compress → DEEP

영감: RisuAI의 SupaMemory (계층적 요약) + HypaMemory (비율 기반 컨텍스트)
=========================================================
"""

import json
import logging
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

# Google Gemini API
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

# =========================================================
# 상수 정의
# =========================================================

# 발효 트리거 임계값
FRESH_THRESHOLD = 40          # FRESH 최대 개수 (초과 시 발효)
FERMENT_CHUNK_SIZE = 20       # 한 번에 발효할 메시지 수
FERMENTED_THRESHOLD = 5       # FERMENTED 최대 개수 (초과 시 DEEP 압축)

# 컨텍스트 비율 (HypaMemory V3 참고)
DEEP_RATIO = 0.10             # 10% - 장기 기억
FERMENTED_RATIO = 0.30        # 30% - 중기 기억
FRESH_RATIO = 0.60            # 60% - 최근 대화

# 토큰 추정용
MAX_CONTEXT_TOKENS = 8000     # 메모리용 최대 토큰 (전체 컨텍스트의 일부)
CHARS_PER_TOKEN = 3.5         # 한글/영어 혼합 기준

# 요약 목표 길이 (문자)
FERMENT_SUMMARY_LENGTH = 500  # 각 발효 요약 목표 길이
DEEP_SUMMARY_LENGTH = 1000    # DEEP 메모리 목표 길이

# 로깅
logger = logging.getLogger("Fermentation")


# =========================================================
# TRPG 특화 요약 프롬프트
# =========================================================

FERMENT_PROMPT = """
[TRPG Session Summarizer - Fermentation]

Write a brief overview of the key events in natural sentences.

### CRITICAL RULES
1. **Only use information explicitly mentioned** - Do not infer or add details
2. **Include dates and temporal indicators** - "Day 3", "that evening", "next morning"
3. **Maintain objective perspective** - No interpretation or explanation
4. **Use past tense** - Everything happened already
5. **Write in Korean** - 한국어로 작성

### MUST PRESERVE
- NPC names and relationships
- Location changes (where → where)
- Key events, decisions, discoveries
- Time flow (which day, time of day)
- Character state changes (injuries, items, emotions)
- Unresolved plot hooks

### EXCLUDE
- Casual dialogue ("안녕", "고마워")
- Repetitive action descriptions
- System messages
- Your interpretation of motives

### OUTPUT FORMAT
- Korean, ~500 characters
- Chronological order
- Natural prose sentences (not bullet points)

### EXAMPLE
"3일차 오후, 일행은 검은 숲에 진입했다. 고블린 정찰대와 조우하여 전투가 벌어졌고, 리엘이 부상을 입었으나 결국 승리했다. 고블린에게서 '붉은 문장'이 새겨진 편지를 발견했는데, 누군가 고블린을 고용한 정황이었다. 이후 숲 깊숙이 폐허가 된 탑을 발견했다."
"""

DEEP_COMPRESS_PROMPT = """
[TRPG Session Ultra-Compressor - Deep Memory]

Merge multiple session summaries into ONE cohesive historical record.

### CRITICAL RULES
1. **Only use information explicitly stated** - No inference
2. **Maintain chronological flow** - Use temporal markers
3. **Objective perspective only** - Facts, not interpretation
4. **Past tense throughout**
5. **Write in Korean** - 한국어로 작성

### MUST PRESERVE
- Main story arc skeleton
- Key NPCs only (story-critical)
- Permanent world changes
- Character growth (passives, titles, key items)
- Unresolved MAIN plot hooks

### EXCLUDE
- Minor combat details
- One-time NPCs
- Resolved side quest details
- Interpretations or speculation

### OUTPUT FORMAT
- Korean, ~1000 characters
- Chronological + thematic organization
- Natural prose (not lists)

### EXAMPLE
"1주차: 모험가 일행이 왕도를 출발하여 검은 숲을 거쳐 폐탑을 발견했다. 고블린 습격의 배후에 '붉은 문장' 세력이 있음이 드러났다. 폐탑에서 고대 지도를 발견했으며, 리엘이 [숲의 가호] 패시브를 획득했다. 붉은 문장의 정체와 고대 지도가 가리키는 장소는 아직 밝혀지지 않았다."
"""


# =========================================================
# 유틸리티 함수
# =========================================================

def estimate_tokens(text: str) -> int:
    """텍스트의 토큰 수를 추정합니다."""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN)


def format_history_for_summary(history: List[Dict[str, str]]) -> str:
    """히스토리를 요약용 텍스트로 변환합니다."""
    lines = []
    for entry in history:
        role = entry.get("role", "Unknown")
        content = entry.get("content", "")
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


def get_timestamp() -> str:
    """현재 타임스탬프를 반환합니다."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# =========================================================
# 발효 필요 여부 판단
# =========================================================

def should_ferment_fresh(session_data: Dict[str, Any]) -> bool:
    """
    FRESH → FERMENTED 발효가 필요한지 판단합니다.
    
    Returns:
        True if history > FRESH_THRESHOLD
    """
    history = session_data.get("history", [])
    return len(history) > FRESH_THRESHOLD


def should_compress_to_deep(session_data: Dict[str, Any]) -> bool:
    """
    FERMENTED → DEEP 압축이 필요한지 판단합니다.
    
    Returns:
        True if fermented_history > FERMENTED_THRESHOLD
    """
    fermented = session_data.get("fermented_history", [])
    return len(fermented) > FERMENTED_THRESHOLD


# =========================================================
# FRESH → FERMENTED 발효
# =========================================================

async def compress_fresh_to_fermented(
    client,
    model_id: str,
    history: List[Dict[str, str]],
    chunk_size: int = FERMENT_CHUNK_SIZE
) -> Optional[str]:
    """
    오래된 히스토리를 요약하여 FERMENTED 메모리로 변환합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        history: 요약할 히스토리 (가장 오래된 chunk_size개)
        chunk_size: 한 번에 요약할 메시지 수
    
    Returns:
        요약된 텍스트 또는 None
    """
    if not client or not history:
        return None
    
    # 요약할 부분 추출
    to_summarize = history[:chunk_size]
    history_text = format_history_for_summary(to_summarize)
    
    system_instruction = FERMENT_PROMPT
    
    user_prompt = (
        f"### 요약할 대화 내용 ({len(to_summarize)}개 메시지)\n\n"
        f"{history_text}\n\n"
        "위 내용을 TRPG 세션 요약 형식으로 압축해주세요."
    )
    
    try:
        contents = [
            types.Content(role="user", parts=[types.Part(text=user_prompt)])
        ]
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3,  # 낮은 창의성, 사실 보존
            max_output_tokens=1000
        )
        
        response = await client.aio.models.generate_content(
            model=model_id,
            contents=contents,
            config=config
        )
        
        if response and response.text:
            summary = response.text.strip()
            logger.info(f"[Fermentation] FRESH → FERMENTED: {len(to_summarize)}개 → {len(summary)}자")
            return summary
            
    except Exception as e:
        logger.error(f"[Fermentation] 발효 실패: {e}")
    
    return None


# =========================================================
# FERMENTED → DEEP 압축
# =========================================================

async def compress_fermented_to_deep(
    client,
    model_id: str,
    fermented_list: List[Dict[str, Any]],
    current_deep: str = ""
) -> Optional[str]:
    """
    FERMENTED 메모리들을 DEEP 메모리로 초압축합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        fermented_list: FERMENTED 메모리 리스트
        current_deep: 기존 DEEP 메모리 (있으면 통합)
    
    Returns:
        초압축된 DEEP 메모리 텍스트
    """
    if not client or not fermented_list:
        return None
    
    # FERMENTED 내용들 결합
    fermented_texts = []
    for i, entry in enumerate(fermented_list):
        timestamp = entry.get("timestamp", f"기록 {i+1}")
        summary = entry.get("summary", "")
        fermented_texts.append(f"[{timestamp}]\n{summary}")
    
    all_fermented = "\n\n---\n\n".join(fermented_texts)
    
    system_instruction = DEEP_COMPRESS_PROMPT
    
    # 기존 DEEP이 있으면 함께 통합
    context_part = ""
    if current_deep:
        context_part = f"### 기존 DEEP 메모리\n{current_deep}\n\n"
    
    user_prompt = (
        f"{context_part}"
        f"### 통합할 FERMENTED 메모리들 ({len(fermented_list)}개)\n\n"
        f"{all_fermented}\n\n"
        "위 모든 내용을 하나의 핵심 요약으로 통합해주세요."
    )
    
    try:
        contents = [
            types.Content(role="user", parts=[types.Part(text=user_prompt)])
        ]
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,  # 매우 낮은 창의성
            max_output_tokens=2000
        )
        
        response = await client.aio.models.generate_content(
            model=model_id,
            contents=contents,
            config=config
        )
        
        if response and response.text:
            deep_summary = response.text.strip()
            logger.info(f"[Fermentation] FERMENTED → DEEP: {len(fermented_list)}개 → {len(deep_summary)}자")
            return deep_summary
            
    except Exception as e:
        logger.error(f"[Fermentation] DEEP 압축 실패: {e}")
    
    return None


# =========================================================
# 자동 발효 프로세스
# =========================================================

async def auto_ferment(
    client,
    model_id: str,
    session_data: Dict[str, Any],
    save_callback=None
) -> Dict[str, Any]:
    """
    세션 데이터를 검사하고 필요 시 자동으로 발효합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        session_data: 세션 데이터 (수정됨)
        save_callback: 저장 콜백 함수 (선택)
    
    Returns:
        수정된 session_data
    """
    changes_made = False
    
    # 1. fermented_history 필드 확인/생성
    if "fermented_history" not in session_data:
        session_data["fermented_history"] = []
    
    if "deep_memory" not in session_data:
        session_data["deep_memory"] = ""
    
    # 2. FRESH → FERMENTED 발효 체크
    if should_ferment_fresh(session_data):
        logger.info("[Fermentation] FRESH 발효 시작...")
        
        history = session_data["history"]
        
        # 가장 오래된 FERMENT_CHUNK_SIZE개 요약
        summary = await compress_fresh_to_fermented(
            client, model_id, 
            history[:FERMENT_CHUNK_SIZE]
        )
        
        if summary:
            # FERMENTED에 추가
            session_data["fermented_history"].append({
                "timestamp": get_timestamp(),
                "summary": summary,
                "message_count": FERMENT_CHUNK_SIZE
            })
            
            # FRESH에서 제거 (최근 것만 유지)
            session_data["history"] = history[FERMENT_CHUNK_SIZE:]
            changes_made = True
            
            logger.info(f"[Fermentation] FRESH 발효 완료: "
                       f"history {len(history)} → {len(session_data['history'])}")
    
    # 3. FERMENTED → DEEP 압축 체크
    if should_compress_to_deep(session_data):
        logger.info("[Fermentation] DEEP 압축 시작...")
        
        fermented = session_data["fermented_history"]
        current_deep = session_data.get("deep_memory", "")
        
        deep_summary = await compress_fermented_to_deep(
            client, model_id,
            fermented, current_deep
        )
        
        if deep_summary:
            # DEEP 갱신
            session_data["deep_memory"] = deep_summary
            
            # FERMENTED 클리어
            session_data["fermented_history"] = []
            changes_made = True
            
            logger.info(f"[Fermentation] DEEP 압축 완료: "
                       f"fermented {len(fermented)}개 → deep {len(deep_summary)}자")
    
    # 4. 변경사항 저장
    if changes_made and save_callback:
        save_callback()
    
    return session_data


# =========================================================
# 메모리 컨텍스트 빌드
# =========================================================

def build_memory_context(
    session_data: Dict[str, Any],
    max_tokens: int = MAX_CONTEXT_TOKENS
) -> str:
    """
    FERMENTED 메모리를 DIRECTIVE 직전에 배치할 컨텍스트로 생성합니다.
    
    NOTE: DEEP MEMORY는 시스템 프롬프트(persona.py)에 포함되어 HIGH 인식률 위치에 배치됨.
          FERMENTED는 여기서 반환되어 DIRECTIVE 직전 VERY HIGH 위치에 배치됨.
    
    Args:
        session_data: 세션 데이터
        max_tokens: 최대 토큰 수
    
    Returns:
        FERMENTED 메모리 컨텍스트 문자열 (DIRECTIVE 직전 배치용)
    """
    fermented = session_data.get("fermented_history", [])
    
    if not fermented:
        return ""
    
    # FERMENTED MEMORY (중기 기억) - VERY HIGH 인식률 위치용
    max_fermented_chars = int(max_tokens * (FERMENTED_RATIO + DEEP_RATIO) * CHARS_PER_TOKEN)
    
    fermented_texts = []
    total_chars = 0
    
    # 최신 것부터 추가 (역순)
    for entry in reversed(fermented):
        summary = entry.get("summary", "")
        timestamp = entry.get("timestamp", "")
        
        entry_text = f"[{timestamp}] {summary}"
        
        if total_chars + len(entry_text) > max_fermented_chars:
            break
        
        fermented_texts.insert(0, entry_text)  # 시간순 유지
        total_chars += len(entry_text)
    
    if not fermented_texts:
        return ""
    
    return (
        f"### [FERMENTED MEMORY - 중기 기억]\n"
        f"**CRITICAL: 아래 기억은 스토리 연속성을 위해 반드시 참조해야 합니다.**\n\n" +
        "\n---\n".join(fermented_texts) +
        "\n\n"
    )


# =========================================================
# 메모리 상태 조회
# =========================================================

def get_memory_stats(session_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    현재 메모리 상태 통계를 반환합니다.
    
    Returns:
        {
            "fresh_count": int,
            "fermented_count": int,
            "deep_length": int,
            "total_estimated_tokens": int,
            "needs_fermentation": bool,
            "needs_deep_compression": bool
        }
    """
    history = session_data.get("history", [])
    fermented = session_data.get("fermented_history", [])
    deep = session_data.get("deep_memory", "")
    
    # 토큰 추정
    fresh_tokens = sum(
        estimate_tokens(h.get("content", "")) 
        for h in history
    )
    fermented_tokens = sum(
        estimate_tokens(f.get("summary", ""))
        for f in fermented
    )
    deep_tokens = estimate_tokens(deep)
    
    return {
        "fresh_count": len(history),
        "fermented_count": len(fermented),
        "deep_length": len(deep),
        "fresh_tokens": fresh_tokens,
        "fermented_tokens": fermented_tokens,
        "deep_tokens": deep_tokens,
        "total_estimated_tokens": fresh_tokens + fermented_tokens + deep_tokens,
        "needs_fermentation": len(history) > FRESH_THRESHOLD,
        "needs_deep_compression": len(fermented) > FERMENTED_THRESHOLD
    }


def format_memory_status(session_data: Dict[str, Any]) -> str:
    """
    메모리 상태를 사람이 읽기 좋은 형식으로 반환합니다.
    """
    stats = get_memory_stats(session_data)
    
    status_lines = [
        "📊 **메모리 상태**",
        f"  🔵 FRESH: {stats['fresh_count']}개 (~{stats['fresh_tokens']} 토큰)",
        f"  🟡 FERMENTED: {stats['fermented_count']}개 (~{stats['fermented_tokens']} 토큰)",
        f"  🟣 DEEP: {stats['deep_length']}자 (~{stats['deep_tokens']} 토큰)",
        f"  📈 총 추정 토큰: {stats['total_estimated_tokens']}"
    ]
    
    if stats["needs_fermentation"]:
        status_lines.append("  ⚠️ 발효 필요 (FRESH 초과)")
    if stats["needs_deep_compression"]:
        status_lines.append("  ⚠️ DEEP 압축 필요 (FERMENTED 초과)")
    
    return "\n".join(status_lines)


# =========================================================
# 수동 발효 트리거 (디버그/관리용)
# =========================================================

async def force_ferment(
    client,
    model_id: str,
    session_data: Dict[str, Any],
    save_callback=None
) -> Tuple[bool, str]:
    """
    강제로 발효를 실행합니다 (임계값 무시).
    
    Returns:
        (성공 여부, 메시지)
    """
    history = session_data.get("history", [])
    
    if len(history) < 5:
        return False, "발효할 히스토리가 부족합니다 (최소 5개 필요)"
    
    # fermented_history 필드 확인
    if "fermented_history" not in session_data:
        session_data["fermented_history"] = []
    
    # 절반을 발효
    chunk_size = max(5, len(history) // 2)
    
    summary = await compress_fresh_to_fermented(
        client, model_id,
        history[:chunk_size],
        chunk_size
    )
    
    if summary:
        session_data["fermented_history"].append({
            "timestamp": get_timestamp(),
            "summary": summary,
            "message_count": chunk_size
        })
        session_data["history"] = history[chunk_size:]
        
        if save_callback:
            save_callback()
        
        return True, f"발효 완료: {chunk_size}개 메시지 → 요약 {len(summary)}자"
    
    return False, "발효 실패"


async def force_deep_compress(
    client,
    model_id: str,
    session_data: Dict[str, Any],
    save_callback=None
) -> Tuple[bool, str]:
    """
    강제로 DEEP 압축을 실행합니다 (임계값 무시).
    
    Returns:
        (성공 여부, 메시지)
    """
    fermented = session_data.get("fermented_history", [])
    
    if not fermented:
        return False, "압축할 FERMENTED 메모리가 없습니다"
    
    current_deep = session_data.get("deep_memory", "")
    
    deep_summary = await compress_fermented_to_deep(
        client, model_id,
        fermented, current_deep
    )
    
    if deep_summary:
        session_data["deep_memory"] = deep_summary
        session_data["fermented_history"] = []
        
        if save_callback:
            save_callback()
        
        return True, f"DEEP 압축 완료: {len(fermented)}개 → {len(deep_summary)}자"
    
    return False, "DEEP 압축 실패"


# =========================================================
# 초기화 및 마이그레이션
# =========================================================

def ensure_memory_fields(session_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    세션 데이터에 메모리 관련 필드가 있는지 확인하고 없으면 추가합니다.
    
    Args:
        session_data: 세션 데이터
    
    Returns:
        업데이트된 session_data
    """
    if "fermented_history" not in session_data:
        session_data["fermented_history"] = []
    
    if "deep_memory" not in session_data:
        session_data["deep_memory"] = ""
    
    return session_data


# =========================================================
# CONTEXT CACHING SYSTEM
# Gemini API Context Caching for System Prompts
# =========================================================

# 캐싱 상수
CACHE_MIN_TOKENS = 32768  # Gemini 최소 캐싱 토큰
CACHE_DEFAULT_TTL_MINUTES = 60  # 기본 TTL (1시간)
CACHE_SESSION_TTL_MINUTES = 180  # 세션용 TTL (3시간)

# 채널별 캐시 저장소 (메모리)
_channel_caches: Dict[str, Dict[str, Any]] = {}


def estimate_content_tokens(content: str) -> int:
    """컨텐츠의 토큰 수를 추정합니다."""
    if not content:
        return 0
    # 한글/영어 혼합 기준 약 3.5자당 1토큰
    return int(len(content) / CHARS_PER_TOKEN)


def should_use_caching(lore_text: str, deep_memory: str = "") -> bool:
    """
    캐싱을 사용해야 하는지 판단합니다.
    최소 32,768 토큰이 필요합니다.
    
    Args:
        lore_text: 로어 텍스트
        deep_memory: DEEP 메모리
    
    Returns:
        캐싱 사용 여부
    """
    total_content = lore_text + (deep_memory or "")
    estimated_tokens = estimate_content_tokens(total_content)
    
    logger.debug(f"[Caching] 추정 토큰: {estimated_tokens} (최소: {CACHE_MIN_TOKENS})")
    
    return estimated_tokens >= CACHE_MIN_TOKENS


async def create_context_cache(
    client,
    model_id: str,
    channel_id: str,
    lore_text: str,
    rule_text: str = "",
    deep_memory: str = "",
    system_instruction: str = "",
    ttl_minutes: int = CACHE_SESSION_TTL_MINUTES
) -> Optional[str]:
    """
    컨텍스트 캐시를 생성합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID (버전 suffix 필요: gemini-1.5-flash-001)
        channel_id: 채널 ID (캐시 식별용)
        lore_text: 로어 텍스트
        rule_text: 룰 텍스트
        deep_memory: DEEP 메모리
        system_instruction: 시스템 인스트럭션
        ttl_minutes: 캐시 TTL (분)
    
    Returns:
        캐시 이름 또는 None
    """
    if not client:
        return None
    
    # 캐싱 가능 여부 확인
    if not should_use_caching(lore_text, deep_memory):
        logger.info(f"[Caching] 토큰 부족으로 캐싱 스킵 - {channel_id}")
        return None
    
    try:
        # 캐시할 컨텐츠 구성
        cache_content = f"""
{system_instruction}

<Deep_Memory priority="HIGH">
{deep_memory if deep_memory else "(No deep memory yet)"}
</Deep_Memory>

<World_Data>
### Lore (세계관)
{lore_text}

### Rules (규칙)
{rule_text if rule_text else "(Standard TRPG rules apply)"}
</World_Data>
"""
        
        # TTL을 timedelta로 변환
        from datetime import timedelta
        ttl = timedelta(minutes=ttl_minutes)
        
        # 캐시 생성
        cache = client.caches.create(
            model=model_id,
            config=types.CreateCachedContentConfig(
                display_name=f"lorekeeper-{channel_id}",
                system_instruction=system_instruction,
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part(text=cache_content)]
                    )
                ],
                ttl=ttl
            )
        )
        
        # 채널별 캐시 저장
        _channel_caches[channel_id] = {
            "cache_name": cache.name,
            "created_at": get_timestamp(),
            "ttl_minutes": ttl_minutes,
            "lore_hash": hash(lore_text),  # 로어 변경 감지용
            "deep_hash": hash(deep_memory or "")
        }
        
        logger.info(f"[Caching] 캐시 생성 완료 - {channel_id}: {cache.name}")
        return cache.name
        
    except Exception as e:
        logger.error(f"[Caching] 캐시 생성 실패 - {channel_id}: {e}")
        return None


def get_cached_content_name(channel_id: str) -> Optional[str]:
    """
    채널의 캐시 이름을 반환합니다.
    
    Args:
        channel_id: 채널 ID
    
    Returns:
        캐시 이름 또는 None
    """
    cache_info = _channel_caches.get(channel_id)
    if cache_info:
        return cache_info.get("cache_name")
    return None


def is_cache_valid(
    channel_id: str, 
    lore_text: str, 
    deep_memory: str = ""
) -> bool:
    """
    캐시가 유효한지 확인합니다.
    로어나 DEEP 메모리가 변경되면 무효화됩니다.
    
    Args:
        channel_id: 채널 ID
        lore_text: 현재 로어 텍스트
        deep_memory: 현재 DEEP 메모리
    
    Returns:
        캐시 유효 여부
    """
    cache_info = _channel_caches.get(channel_id)
    if not cache_info:
        return False
    
    # 해시 비교로 변경 감지
    current_lore_hash = hash(lore_text)
    current_deep_hash = hash(deep_memory or "")
    
    if cache_info.get("lore_hash") != current_lore_hash:
        logger.info(f"[Caching] 로어 변경 감지 - {channel_id}")
        return False
    
    if cache_info.get("deep_hash") != current_deep_hash:
        logger.info(f"[Caching] DEEP 메모리 변경 감지 - {channel_id}")
        return False
    
    return True


def invalidate_cache(channel_id: str) -> bool:
    """
    채널의 캐시를 무효화합니다.
    
    Args:
        channel_id: 채널 ID
    
    Returns:
        무효화 성공 여부
    """
    if channel_id in _channel_caches:
        del _channel_caches[channel_id]
        logger.info(f"[Caching] 캐시 무효화 - {channel_id}")
        return True
    return False


async def delete_context_cache(client, channel_id: str) -> bool:
    """
    Gemini API에서 캐시를 삭제합니다.
    
    Args:
        client: Gemini 클라이언트
        channel_id: 채널 ID
    
    Returns:
        삭제 성공 여부
    """
    cache_name = get_cached_content_name(channel_id)
    if not cache_name:
        return False
    
    try:
        client.caches.delete(name=cache_name)
        invalidate_cache(channel_id)
        logger.info(f"[Caching] 캐시 삭제 완료 - {channel_id}")
        return True
    except Exception as e:
        logger.error(f"[Caching] 캐시 삭제 실패 - {channel_id}: {e}")
        invalidate_cache(channel_id)  # 로컬은 정리
        return False


def get_cache_stats() -> Dict[str, Any]:
    """
    전체 캐시 통계를 반환합니다.
    
    Returns:
        캐시 통계 딕셔너리
    """
    return {
        "total_caches": len(_channel_caches),
        "channels": list(_channel_caches.keys()),
        "details": {
            ch: {
                "created_at": info.get("created_at"),
                "ttl_minutes": info.get("ttl_minutes")
            }
            for ch, info in _channel_caches.items()
        }
    }


async def get_or_create_cache(
    client,
    model_id: str,
    channel_id: str,
    lore_text: str,
    rule_text: str = "",
    deep_memory: str = "",
    system_instruction: str = ""
) -> Optional[str]:
    """
    캐시를 가져오거나 없으면 생성합니다.
    
    Args:
        client: Gemini 클라이언트
        model_id: 모델 ID
        channel_id: 채널 ID
        lore_text: 로어 텍스트
        rule_text: 룰 텍스트
        deep_memory: DEEP 메모리
        system_instruction: 시스템 인스트럭션
    
    Returns:
        캐시 이름 또는 None
    """
    # 기존 캐시 확인
    if is_cache_valid(channel_id, lore_text, deep_memory):
        cache_name = get_cached_content_name(channel_id)
        if cache_name:
            logger.debug(f"[Caching] 기존 캐시 사용 - {channel_id}")
            return cache_name
    
    # 새 캐시 생성
    return await create_context_cache(
        client, model_id, channel_id,
        lore_text, rule_text, deep_memory,
        system_instruction
    )

