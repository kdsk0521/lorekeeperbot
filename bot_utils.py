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

def clean_json_text(text: str) -> str:
    """JSON 문자열에서 코드 블록 마커 + trailing comma 등을 제거합니다."""
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
