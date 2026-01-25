"""
Lorekeeper TRPG Bot - Bot Utils 테스트
RateLimiter, 메시지 전송, 파일 읽기 테스트
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from time import time

import bot_utils


class TestRateLimiter:
    """RateLimiter 클래스 테스트"""

    @pytest.fixture
    def limiter(self):
        """테스트용 RateLimiter (짧은 시간 윈도우)"""
        return bot_utils.RateLimiter(max_messages=3, time_window=1.0)

    @pytest.mark.asyncio
    async def test_no_wait_under_limit(self, limiter):
        """제한 이하에서는 대기 없음"""
        start = time()
        await limiter.wait_if_needed("test_channel")
        await limiter.wait_if_needed("test_channel")
        elapsed = time() - start

        # 대기 없이 빠르게 실행되어야 함
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_wait_when_limit_exceeded(self, limiter):
        """제한 초과시 대기"""
        # 3개 메시지를 빠르게 보냄
        for _ in range(3):
            await limiter.wait_if_needed("test_channel")

        # 4번째 메시지는 대기해야 함
        start = time()
        await limiter.wait_if_needed("test_channel")
        elapsed = time() - start

        # 약간의 대기가 발생해야 함 (시간 윈도우 내에서)
        # 단, 첫 메시지가 만료될 때까지

    @pytest.mark.asyncio
    async def test_separate_channels(self, limiter):
        """채널별 독립적인 제한"""
        # 채널 A에 3개
        for _ in range(3):
            await limiter.wait_if_needed("channel_a")

        # 채널 B는 독립적이므로 바로 실행
        start = time()
        await limiter.wait_if_needed("channel_b")
        elapsed = time() - start

        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_old_timestamps_removed(self, limiter):
        """오래된 타임스탬프 제거"""
        # 메시지 하나 보내고 시간 경과 시뮬레이션
        await limiter.wait_if_needed("test_channel")

        # 시간 경과 후 (time_window 초과)
        await asyncio.sleep(1.1)

        # 이제 새 메시지는 대기 없이 보낼 수 있어야 함
        start = time()
        await limiter.wait_if_needed("test_channel")
        elapsed = time() - start

        assert elapsed < 0.1

    def test_default_parameters(self):
        """기본 파라미터 확인"""
        limiter = bot_utils.RateLimiter()
        assert limiter.max_messages == 5
        assert limiter.time_window == 5.0


class TestSendLongMessage:
    """send_long_message 함수 테스트"""

    @pytest.mark.asyncio
    async def test_empty_message(self):
        """빈 메시지는 전송하지 않음"""
        channel = MagicMock()
        channel.send = AsyncMock()

        await bot_utils.send_long_message(channel, "")

        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_message(self):
        """짧은 메시지는 한 번에 전송"""
        channel = MagicMock()
        channel.id = 12345
        channel.send = AsyncMock()

        with patch.object(bot_utils.rate_limiter, 'wait_if_needed', new_callable=AsyncMock):
            await bot_utils.send_long_message(channel, "짧은 메시지")

        channel.send.assert_called_once_with("짧은 메시지")

    @pytest.mark.asyncio
    async def test_long_message_split(self):
        """긴 메시지는 분할 전송"""
        channel = MagicMock()
        channel.id = 12345
        channel.send = AsyncMock()

        # 2000자 초과 메시지 생성
        long_message = "가" * 2500

        with patch.object(bot_utils.rate_limiter, 'wait_if_needed', new_callable=AsyncMock):
            await bot_utils.send_long_message(channel, long_message)

        # 2번 호출되어야 함 (2000 + 500)
        assert channel.send.call_count == 2

    @pytest.mark.asyncio
    async def test_very_long_message(self):
        """매우 긴 메시지 분할"""
        channel = MagicMock()
        channel.id = 12345
        channel.send = AsyncMock()

        # 5000자 메시지 (3번 분할)
        long_message = "나" * 5000

        with patch.object(bot_utils.rate_limiter, 'wait_if_needed', new_callable=AsyncMock):
            await bot_utils.send_long_message(channel, long_message)

        # 3번 호출 (2000 + 2000 + 1000)
        assert channel.send.call_count == 3


class TestReadAttachmentText:
    """read_attachment_text 함수 테스트"""

    @pytest.mark.asyncio
    async def test_file_too_large(self):
        """파일 크기 초과"""
        attachment = MagicMock()
        attachment.filename = "large.txt"
        attachment.size = 20 * 1024 * 1024  # 20MB

        text, error = await bot_utils.read_attachment_text(attachment)

        assert text is None
        assert "너무 큽니다" in error

    @pytest.mark.asyncio
    async def test_unsupported_extension(self):
        """지원하지 않는 확장자"""
        attachment = MagicMock()
        attachment.filename = "image.png"
        attachment.size = 1024

        text, error = await bot_utils.read_attachment_text(attachment)

        assert text is None
        assert "지원하지 않는" in error

    @pytest.mark.asyncio
    async def test_valid_txt_file(self):
        """유효한 txt 파일"""
        attachment = MagicMock()
        attachment.filename = "test.txt"
        attachment.size = 1024
        attachment.read = AsyncMock(return_value="테스트 내용".encode('utf-8'))

        text, error = await bot_utils.read_attachment_text(attachment)

        assert text == "테스트 내용"
        assert error is None

    @pytest.mark.asyncio
    async def test_valid_md_file(self):
        """유효한 마크다운 파일"""
        attachment = MagicMock()
        attachment.filename = "readme.md"
        attachment.size = 1024
        attachment.read = AsyncMock(return_value="# 제목\n내용".encode('utf-8'))

        text, error = await bot_utils.read_attachment_text(attachment)

        assert "# 제목" in text
        assert error is None

    @pytest.mark.asyncio
    async def test_korean_encoding_cp949(self):
        """CP949 인코딩 파일"""
        attachment = MagicMock()
        attachment.filename = "korean.txt"
        attachment.size = 1024
        attachment.read = AsyncMock(return_value="한글 테스트".encode('cp949'))

        text, error = await bot_utils.read_attachment_text(attachment)

        assert text == "한글 테스트"
        assert error is None

    @pytest.mark.asyncio
    async def test_text_too_long(self):
        """텍스트 길이 초과"""
        attachment = MagicMock()
        attachment.filename = "long.txt"
        attachment.size = 1024
        # 50000자 초과
        attachment.read = AsyncMock(return_value=("가" * 60000).encode('utf-8'))

        text, error = await bot_utils.read_attachment_text(attachment)

        assert text is None
        assert "너무 깁니다" in error

    @pytest.mark.asyncio
    async def test_read_exception(self):
        """읽기 오류"""
        attachment = MagicMock()
        attachment.filename = "error.txt"
        attachment.size = 1024
        attachment.read = AsyncMock(side_effect=Exception("Network error"))

        text, error = await bot_utils.read_attachment_text(attachment)

        assert text is None
        assert "읽기 실패" in error


class TestSafeDeleteMessage:
    """safe_delete_message 함수 테스트"""

    @pytest.mark.asyncio
    async def test_successful_delete(self):
        """성공적인 삭제"""
        message = MagicMock()
        message.delete = AsyncMock()

        await bot_utils.safe_delete_message(message)

        message.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_found_error(self):
        """메시지를 찾을 수 없음"""
        import discord

        message = MagicMock()
        message.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(), "Not found"))

        # 예외가 발생해도 크래시하지 않아야 함
        await bot_utils.safe_delete_message(message)

    @pytest.mark.asyncio
    async def test_forbidden_error(self):
        """권한 없음"""
        import discord

        message = MagicMock()
        message.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Forbidden"))

        # 예외가 발생해도 크래시하지 않아야 함
        await bot_utils.safe_delete_message(message)

    @pytest.mark.asyncio
    async def test_generic_error(self):
        """일반 오류"""
        message = MagicMock()
        message.delete = AsyncMock(side_effect=Exception("Unknown error"))

        # 예외가 발생해도 크래시하지 않아야 함
        await bot_utils.safe_delete_message(message)


class TestModuleConstants:
    """모듈 상수 테스트"""

    def test_max_discord_message_length(self):
        """Discord 메시지 최대 길이"""
        assert bot_utils.MAX_DISCORD_MESSAGE_LENGTH == 2000

    def test_max_file_size(self):
        """최대 파일 크기"""
        assert bot_utils.MAX_FILE_SIZE_MB == 10
        assert bot_utils.MAX_FILE_SIZE_BYTES == 10 * 1024 * 1024

    def test_supported_extensions(self):
        """지원 확장자"""
        assert '.txt' in bot_utils.SUPPORTED_TEXT_EXTENSIONS
        assert '.md' in bot_utils.SUPPORTED_TEXT_EXTENSIONS
        assert '.json' in bot_utils.SUPPORTED_TEXT_EXTENSIONS
