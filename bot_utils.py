"""
Lorekeeper TRPG Bot - Utility Module
공통적으로 사용되는 메시지 전송, 파일 읽기, 삭제 관련 유틸리티입니다.
"""

import discord
import logging
import asyncio
from typing import Tuple, Optional, Dict, List
from collections import defaultdict, deque
from time import time

import config

# =========================================================
# Rate Limiter
# =========================================================
class RateLimiter:
    """Discord API rate limiting 방지를 위한 간단한 rate limiter"""

    def __init__(self, max_messages: int = 5, time_window: float = 5.0):
        self.max_messages = max_messages
        self.time_window = time_window
        self.message_times: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_messages))

    async def wait_if_needed(self, channel_id: str) -> None:
        """필요 시 대기하여 rate limit을 준수합니다."""
        now = time()
        times = self.message_times[channel_id]

        # 오래된 타임스탬프 제거
        while times and now - times[0] > self.time_window:
            times.popleft()

        # Rate limit 초과 시 대기
        if len(times) >= self.max_messages:
            oldest = times[0]
            wait_time = self.time_window - (now - oldest)
            if wait_time > 0:
                logging.debug(f"Rate limit 대기: {wait_time:.2f}초")
                await asyncio.sleep(wait_time)

        # 현재 메시지 타임스탬프 기록
        self.message_times[channel_id].append(time())

rate_limiter = RateLimiter()


async def send_long_message(channel: discord.TextChannel, text: str) -> List[discord.Message]:
    """2000자가 넘는 메시지를 나누어 전송하는 함수. 전송된 메시지 리스트 반환."""
    if not text:
        return []

    channel_id = str(channel.id)
    sent_messages: List[discord.Message] = []

    if len(text) <= config.MAX_DISCORD_MESSAGE_LENGTH:
        await rate_limiter.wait_if_needed(channel_id)
        msg = await channel.send(text)
        return [msg]

    # 메시지 분할 전송
    for i in range(0, len(text), config.MAX_DISCORD_MESSAGE_LENGTH):
        chunk = text[i:i + config.MAX_DISCORD_MESSAGE_LENGTH]
        await rate_limiter.wait_if_needed(channel_id)
        msg = await channel.send(chunk)
        sent_messages.append(msg)
    
    return sent_messages


async def read_attachment_text(attachment: discord.Attachment) -> Tuple[Optional[str], Optional[str]]:
    """
    첨부파일에서 텍스트를 읽어옵니다.

    Returns:
        Tuple[Optional[str], Optional[str]]: (텍스트 내용, 에러 메시지)
    """
    filename_lower = attachment.filename.lower()

    # 파일 크기 확인
    if attachment.size > config.MAX_FILE_SIZE_BYTES:
        return None, f"⚠️ 파일이 너무 큽니다. 최대 크기: {config.MAX_FILE_SIZE_MB}MB"

    # 지원되는 확장자인지 확인
    if not any(filename_lower.endswith(ext) for ext in config.SUPPORTED_TEXT_EXTENSIONS):
        return None, f"⚠️ **지원하지 않는 파일입니다.**\n지원 확장자: {', '.join(config.SUPPORTED_TEXT_EXTENSIONS)}"

    try:
        data = await attachment.read()

        # 여러 인코딩 시도 (한국어 파일 지원)
        encodings = ['utf-8-sig', 'utf-8', 'cp949', 'euc-kr']
        text = None
        last_error = None

        for encoding in encodings:
            try:
                text = data.decode(encoding)
                logging.info(f"파일 '{attachment.filename}' 인코딩: {encoding}")
                break
            except (UnicodeDecodeError, LookupError) as e:
                last_error = e
                continue

        if text is None:
            return None, f"⚠️ 파일 `{attachment.filename}` 읽기 실패: 지원하지 않는 인코딩입니다."

        # Mojibake 자동 복구: UTF-8이 Latin-1/CP1252로 잘못 해석된 경우
        # Single: "그림" → "ê·¸ë¦¼"  Double: "오웬" → "ì˜¤ì›¬" → "Ã¬ËÂ¤Ã¬âºÂ¬"
        _has_kr = lambda t: any('\uac00' <= c <= '\ud7a3' for c in t)
        if not _has_kr(text):
            repaired = text
            for round_num in range(3):
                try:
                    repaired = repaired.encode('cp1252').decode('utf-8')
                except (UnicodeEncodeError, UnicodeDecodeError):
                    try:
                        repaired = repaired.encode('latin-1').decode('utf-8')
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        break
                if _has_kr(repaired):
                    text = repaired
                    logging.info(f"파일 '{attachment.filename}' mojibake 자동 복구 ({round_num+1}단계)")
                    break

        # 텍스트 길이 검증
        if len(text) > config.MAX_TEXT_INPUT_LENGTH:
            return None, f"⚠️ 파일 내용이 너무 깁니다. 최대 {config.MAX_TEXT_INPUT_LENGTH:,}자까지 지원합니다."

        return text, None
    except Exception as e:
        return None, f"⚠️ 파일 `{attachment.filename}` 읽기 실패: {e}"


async def safe_delete_message(message: discord.Message) -> None:
    """메시지를 안전하게 삭제합니다."""
    try:
        await message.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        logging.warning("메시지 삭제 권한이 없습니다.")
    except Exception as e:
        logging.warning(f"메시지 삭제 실패: {e}")

def cap_llm_delta(value, source: str, field: str, *, subject: str = ""):
    """[C1 2026-08-01] LLM이 제안한 수치 델타를 **선언 범위로** 자른다.

    왜 필요한가 — 범위 클램프는 델타 클램프가 아니다.
    `max(0, min(100, cur + delta))`는 결과가 0~100 안에만 있으면 통과시키므로,
    모델이 한 번 크게 뱉으면 depth 5 → 100이 한 턴에 성립한다. 크래시도 안 나고
    무결성 검사도 통과해서 산문에만 "갑자기 가까워진 것처럼" 나온다(조용한 병).
    프롬프트에 범위를 적는 것만으로는 집행이 아니다 — SimCore v0.38.3의 교훈:
    "AI의 협조가 아니라 구조로 막는다."

    ⚠ **주체 라벨이 있는 이유**: 같은 세터를 코드도 쓴다(다운타임 사교 +10~15,
    NPC 시트 initial_depth, trajectory 맵). 무차별 캡은 정상 경로를 자른다.
    그래서 캡은 `source`가 `config.LLM_DELTA_CAPS`에 등재된 **LLM 경로에만** 걸린다.
    코드 소스는 source를 비워 호출하면 무캡(기본값).

    잘릴 때 조용히 자르지 않고 WARNING 1줄을 남긴다 — 그래야 "모델이 얼마나 자주
    선언을 넘는가"가 사후 판독된다(조작면 순증 0, 화면 변화 0).

    Returns: (capped_value, was_clamped)
    """
    import config as _cfg
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value, False
    caps = getattr(_cfg, 'LLM_DELTA_CAPS', {}).get(source)
    if not caps:
        return value, False          # 등재되지 않은 주체(=코드 소스) → 무캡
    bounds = caps.get(field)
    if not bounds:
        return value, False
    lo, hi = bounds
    capped = max(lo, min(hi, v))
    if capped == v:
        return value, False
    logging.warning(
        "[DeltaCap] %s.%s%s: %s → %s (선언 %s~%s 초과)",
        source, field, f" [{subject}]" if subject else "", v, capped, lo, hi,
    )
    return (int(capped) if isinstance(value, int) else capped), True


def clean_json_text(text: str) -> str:
    """JSON 문자열에서 코드 블록 마커 + trailing comma + 자연어 prefix/suffix 등을 제거합니다."""
    import re
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # JS-style 한줄 주석 제거 (문자열 내부가 아닌 경우)
    text = re.sub(r'(?m)^\s*//.*$', '', text)
    # Trailing comma 제거: ,} → }  ,] → ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # 자연어 prefix/suffix 제거: 첫 { 또는 [ 부터 마지막 } 또는 ] 까지 추출
    # 예: "Here is the JSON:\n\n{...}\nThat concludes it." → "{...}"
    m = re.search(r'[\{\[]', text)
    if m:
        start = m.start()
        # 루트 괄호 종료 위치 = 마지막 } 또는 ] (둘 중 더 뒤)
        last_brace = text.rfind('}')
        last_bracket = text.rfind(']')
        last = max(last_brace, last_bracket)
        if last > start:
            text = text[start:last + 1]
    return text


def repair_json(text: str) -> str:
    """Flash 모델의 불완전/비표준 JSON을 수리합니다."""
    import re
    # 1) 제어문자 제거
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # 2) JS 리터럴 → JSON
    text = re.sub(r'\bNaN\b', 'null', text)
    text = re.sub(r'\bInfinity\b', '999999', text)
    text = re.sub(r'\bundefined\b', 'null', text)
    # 2.5) [DeepSeek 2026-06-10] 값 뒤 괄호 주석 제거 — deepseek-v4가 JSON 값에 해설을 다는 버릇.
    #   예: "pc_thought": true ("...라는 생각),   /   "self_opacity": null (분석 불가)",
    #   bare 리터럴(true/false/null/숫자) 뒤 (...) [+잔여 따옴표] 가 구분자 앞에 오면 주석으로 보고 삭제.
    #   파싱 실패 시에만 도는 수리 경로라 문자열 내부 오탐 위험 낮음.
    text = re.sub(
        r'\b(true|false|null|-?\d+(?:\.\d+)?)\s*\([^)]*\)\s*"?(?=\s*[,}\]\n])',
        r'\1', text)
    #   닫는 따옴표 뒤 괄호 주석: "...value" (해설), → "...value",
    text = re.sub(r'(")\s*\([^()"]*\)(?=\s*[,}\]\n])', r'\1', text)
    # 2.6) [GLM 2026-07-27] 같은 버릇의 **엠대쉬/주석기호 판**. 2.5는 괄호만 잡아서 아래를 놓쳤고,
    #   놓치면 수리가 아니라 **파싱 전면 실패**(재시도까지 소진 → Theoria 분석 통째로 유실)다.
    #   실측 로그: "self_opacity": null — automaton; no self-model to be opaque about ...",
    #   구분자를 :뒤 bare 리터럴로 한정(룩비하인드) — 문자열 값 안의 엠대쉬("tense — jaw tight")는 불가침.
    #   줄 끝까지 삼키되 꼬리 콤마는 보존. ASCII 하이픈은 공백에 둘러싸인 경우만(음수 오탐 방지).
    text = re.sub(
        r'(?<=:)(\s*)(true|false|null|-?\d+(?:\.\d+)?)(?:\s*[—–]|\s+-|\s*//|\s*#)'
        r'[^\n]*?(,?)(?=\s*(?:\n|[}\]]))',
        r'\1\2\3', text)
    # 2.7) [GLM 2026-07-27] 키 뒤 스트레이 `":` — 콜론을 두 번 찍는 버릇.
    #   실측 로그: "for_or_against":": "for"  →  "for_or_against": "for"
    text = re.sub(r'":\s*":\s*(?=")', '": ', text)
    # 3) Single-quoted 키 → double-quoted: {'key': → {"key":
    text = re.sub(r"""(?<=[\{,])\s*'([^']+)'\s*:""", r' "\1":', text)
    # 4) Unquoted 키 → double-quoted: {key: → {"key":  ,key: → ,"key":
    text = re.sub(r'(?<=[\{,])\s*([a-zA-Z_]\w*)\s*:', r' "\1":', text)
    # 5) 빈 값 수리: "key": } → "key": null}  /  "key": , → "key": null,
    text = re.sub(r':\s*([}\]])', r': null\1', text)
    text = re.sub(r':\s*,', r': null,', text)
    # 6) Trailing comma 재정리 (3/4/5 단계에서 새로 생길 수 있음)
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # 7) 미완성 문자열 닫기: 홀수 개 따옴표 → 마지막에 " 추가
    quote_count = text.count('"') - text.count('\\"')
    if quote_count % 2 == 1:
        text = text.rstrip() + '"'
    # 7) 잘린 응답 닫기: 열린 {/[ 부족분 보충
    text = text.rstrip().rstrip(',')
    open_b = text.count('{') - text.count('}')
    open_s = text.count('[') - text.count(']')
    if open_s > 0:
        text += ']' * open_s
    if open_b > 0:
        text += '}' * open_b
    return text
