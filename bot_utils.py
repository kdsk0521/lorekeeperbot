"""
Lorekeeper TRPG Bot - Utility Module
공통적으로 사용되는 메시지 전송, 파일 읽기, 삭제 관련 유틸리티입니다.
"""

import discord
import logging
import asyncio
from typing import Tuple, Optional, Dict
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


async def send_long_message(channel: discord.TextChannel, text: str) -> None:
    """2000자가 넘는 메시지를 나누어 전송하는 함수"""
    if not text:
        return

    channel_id = str(channel.id)

    if len(text) <= config.MAX_DISCORD_MESSAGE_LENGTH:
        await rate_limiter.wait_if_needed(channel_id)
        await channel.send(text)
        return

    # 메시지 분할 전송
    for i in range(0, len(text), config.MAX_DISCORD_MESSAGE_LENGTH):
        chunk = text[i:i + config.MAX_DISCORD_MESSAGE_LENGTH]
        await rate_limiter.wait_if_needed(channel_id)
        await channel.send(chunk)


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
        encodings = ['utf-8', 'cp949', 'euc-kr', 'utf-8-sig']
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

        # 텍스트 길이 검증
        if len(text) > config.MAX_TEXT_INPUT_LENGTH:
            return None, f"⚠️ 파일 내용이 너무 깁니다. 최대 {config.MAX_TEXT_INPUT_LENGTH:,}자까지 지원합니다."

        return text, None
    except Exception as e:
        return None, f"⚠️ 파일 `{attachment.filename}` 읽기 실패: {e}"


async def safe_delete_message(message) -> None:
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
    """JSON 문자열에서 코드 블록 마커(```json) 등을 제거합니다."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # 첫 줄이 ```json 등이면 제거
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 마지막 줄이 ``` 이면 제거
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
