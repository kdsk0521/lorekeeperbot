"""
Lorekeeper TRPG Bot - Fermentation System 테스트
메모리 압축, 발효, 컨텍스트 빌드 테스트
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import fermentation


class TestTokenEstimation:
    """토큰 추정 테스트"""

    def test_estimate_tokens_empty(self):
        """빈 텍스트"""
        assert fermentation.estimate_tokens("") == 0
        assert fermentation.estimate_tokens(None) == 0

    def test_estimate_tokens_short(self):
        """짧은 텍스트"""
        # 한글 7자 = 약 2 토큰 (3.5자/토큰)
        result = fermentation.estimate_tokens("안녕하세요")
        assert result > 0
        assert result < 10

    def test_estimate_tokens_long(self):
        """긴 텍스트"""
        text = "가" * 350  # 350자 = 약 100 토큰
        result = fermentation.estimate_tokens(text)
        assert 90 <= result <= 110

    def test_estimate_content_tokens(self):
        """estimate_content_tokens 함수"""
        result = fermentation.estimate_content_tokens("테스트 문자열")
        assert result > 0


class TestHistoryFormatting:
    """히스토리 포맷팅 테스트"""

    def test_format_history_for_summary_empty(self):
        """빈 히스토리"""
        result = fermentation.format_history_for_summary([])
        assert result == ""

    def test_format_history_for_summary(self, sample_history):
        """일반 히스토리"""
        result = fermentation.format_history_for_summary(sample_history)
        assert "[user]:" in result
        assert "[assistant]:" in result
        assert "안녕하세요" in result

    def test_format_history_indexed(self, sample_history):
        """인덱스 기반 포맷팅"""
        result = fermentation.format_history_indexed(sample_history)
        assert "[1]" in result
        assert "[user]:" in result

    def test_format_history_indexed_custom_start(self, sample_history):
        """커스텀 시작 인덱스"""
        result = fermentation.format_history_indexed(sample_history, start_index=10)
        assert "[10]" in result
        assert "[11]" in result


class TestFermentationTriggers:
    """발효 트리거 테스트"""

    def test_should_ferment_fresh_false(self):
        """발효 불필요"""
        session = {"history": [{"role": "user", "content": "test"}] * 10}
        assert fermentation.should_ferment_fresh(session) is False

    def test_should_ferment_fresh_true(self):
        """발효 필요 (임계값 초과)"""
        session = {"history": [{"role": "user", "content": "test"}] * 60}
        assert fermentation.should_ferment_fresh(session) is True

    def test_should_ferment_fresh_empty(self):
        """빈 히스토리"""
        session = {"history": []}
        assert fermentation.should_ferment_fresh(session) is False

    def test_should_compress_to_deep_false(self):
        """DEEP 압축 불필요"""
        session = {"fermented_history": [{"summary": "test"}] * 3}
        assert fermentation.should_compress_to_deep(session) is False

    def test_should_compress_to_deep_true(self):
        """DEEP 압축 필요 (임계값 초과)"""
        session = {"fermented_history": [{"summary": "test"}] * 15}
        assert fermentation.should_compress_to_deep(session) is True


class TestMemoryFieldsInitialization:
    """메모리 필드 초기화 테스트"""

    def test_ensure_memory_fields_new(self):
        """새 세션 데이터"""
        session = {}
        result = fermentation.ensure_memory_fields(session)
        assert "fermented_history" in result
        assert "deep_memory" in result
        assert result["fermented_history"] == []
        assert result["deep_memory"] == ""

    def test_ensure_memory_fields_existing(self):
        """기존 데이터 유지"""
        session = {
            "fermented_history": [{"summary": "existing"}],
            "deep_memory": "existing deep"
        }
        result = fermentation.ensure_memory_fields(session)
        assert len(result["fermented_history"]) == 1
        assert result["deep_memory"] == "existing deep"


class TestBuildFermentedContext:
    """Fermented 컨텍스트 빌드 테스트"""

    def test_build_fermented_context_empty(self):
        """빈 데이터"""
        session = {"fermented_history": [], "deep_memory": ""}
        result = fermentation.build_fermented_context(session)
        assert result == ""

    def test_build_fermented_context_with_deep(self):
        """DEEP 메모리만 있는 경우"""
        session = {
            "fermented_history": [],
            "deep_memory": "과거의 중요한 사건들..."
        }
        result = fermentation.build_fermented_context(session)
        assert "<Fermented>" in result
        assert "Deep Memory" in result
        assert "과거의 중요한 사건들" in result

    def test_build_fermented_context_with_fermented(self):
        """FERMENTED만 있는 경우"""
        session = {
            "fermented_history": [
                {"timestamp": "2024-01-01", "summary": "첫 번째 요약"},
                {"timestamp": "2024-01-02", "summary": "두 번째 요약"}
            ],
            "deep_memory": ""
        }
        result = fermentation.build_fermented_context(session)
        assert "<Fermented>" in result
        assert "Episode Summary" in result
        assert "첫 번째 요약" in result

    def test_build_fermented_context_full(self):
        """DEEP + FERMENTED 모두 있는 경우"""
        session = {
            "fermented_history": [
                {"timestamp": "2024-01-01", "summary": "에피소드 요약"}
            ],
            "deep_memory": "장기 기억 내용"
        }
        result = fermentation.build_fermented_context(session)
        assert "Deep Memory" in result
        assert "Episode Summary" in result


class TestBuildImmediateContext:
    """Immediate 컨텍스트 빌드 테스트"""

    def test_build_immediate_context_empty(self):
        """빈 히스토리"""
        session = {"history": []}
        result = fermentation.build_immediate_context(session)
        assert result == ""

    def test_build_immediate_context(self, sample_history):
        """일반 히스토리"""
        session = {"history": sample_history}
        result = fermentation.build_immediate_context(session)
        assert "<Immediate>" in result
        assert "[user]:" in result
        assert "[assistant]:" in result

    def test_build_immediate_context_limit(self):
        """메시지 수 제한"""
        # 50개 히스토리 생성
        history = [{"role": f"user{i}", "content": f"msg{i}"} for i in range(50)]
        session = {"history": history}

        result = fermentation.build_immediate_context(session, recent_count=10)
        # 마지막 10개만 포함되어야 함
        assert "msg49" in result
        assert "msg40" in result
        assert "msg0" not in result


class TestBuildFullMemoryContext:
    """전체 메모리 컨텍스트 빌드 테스트"""

    def test_build_full_memory_context(self):
        """전체 컨텍스트 빌드"""
        session = {
            "history": [{"role": "user", "content": "최근 대화"}],
            "fermented_history": [{"timestamp": "t1", "summary": "요약"}],
            "deep_memory": "장기 기억"
        }

        fermented, immediate = fermentation.build_full_memory_context(session)

        assert "<Fermented>" in fermented
        assert "<Immediate>" in immediate


class TestMemoryStats:
    """메모리 통계 테스트"""

    def test_get_memory_stats_empty(self):
        """빈 세션"""
        session = {"history": [], "fermented_history": [], "deep_memory": ""}
        stats = fermentation.get_memory_stats(session)

        assert stats["fresh_count"] == 0
        assert stats["fermented_count"] == 0
        assert stats["deep_length"] == 0

    def test_get_memory_stats_with_data(self):
        """데이터가 있는 세션"""
        session = {
            "history": [{"role": "user", "content": "test message"}] * 20,
            "fermented_history": [{"summary": "요약 내용"}] * 3,
            "deep_memory": "장기 기억 " * 50
        }
        stats = fermentation.get_memory_stats(session)

        assert stats["fresh_count"] == 20
        assert stats["fermented_count"] == 3
        assert stats["deep_length"] > 0
        assert stats["total_estimated_tokens"] > 0

    def test_get_memory_display(self):
        """메모리 표시 문자열"""
        session = {
            "history": [{"role": "user", "content": "test"}] * 10,
            "fermented_history": [{"summary": "sum"}] * 2,
            "deep_memory": "deep"
        }
        display = fermentation.get_memory_display(session)

        assert "FRESH" in display
        assert "FERMENTED" in display
        assert "DEEP" in display


class TestCachingLogic:
    """캐싱 로직 테스트"""

    def test_should_use_caching_small(self):
        """작은 컨텐츠 - 캐싱 불필요"""
        result = fermentation.should_use_caching("짧은 로어")
        assert result is False

    def test_should_use_caching_large(self):
        """큰 컨텐츠 - 캐싱 필요"""
        # 4096 토큰 이상 필요 (약 14000자)
        large_lore = "가" * 15000
        result = fermentation.should_use_caching(large_lore)
        assert result is True

    def test_is_cache_valid_no_cache(self):
        """캐시 없음"""
        result = fermentation.is_cache_valid("test_channel", "lore", "deep")
        assert result is False

    def test_invalidate_cache(self):
        """캐시 무효화"""
        # 캐시 수동 설정
        fermentation._channel_caches["test_ch"] = {"cache_name": "test"}

        result = fermentation.invalidate_cache("test_ch")
        assert result is True
        assert "test_ch" not in fermentation._channel_caches

    def test_invalidate_cache_not_exists(self):
        """없는 캐시 무효화"""
        result = fermentation.invalidate_cache("nonexistent")
        assert result is False

    def test_get_cached_content_name(self):
        """캐시 이름 조회"""
        fermentation._channel_caches["ch1"] = {"cache_name": "cache-123"}

        result = fermentation.get_cached_content_name("ch1")
        assert result == "cache-123"

        # 정리
        del fermentation._channel_caches["ch1"]

    def test_get_cache_stats(self):
        """캐시 통계"""
        fermentation._channel_caches["ch1"] = {
            "cache_name": "c1",
            "created_at": "2024-01-01",
            "ttl_minutes": 60
        }

        stats = fermentation.get_cache_stats()
        assert stats["total_caches"] >= 1
        assert "ch1" in stats["channels"]

        # 정리
        del fermentation._channel_caches["ch1"]


class TestAsyncFermentation:
    """비동기 발효 함수 테스트 (API 모킹)"""

    @pytest.mark.asyncio
    @patch('fermentation.types')
    async def test_compress_fresh_to_fermented_empty(self, mock_types):
        """빈 히스토리 압축"""
        client = MagicMock()
        result = await fermentation.compress_fresh_to_fermented(
            client, "model-id", []
        )
        assert result is None

    @pytest.mark.asyncio
    @patch('fermentation.types')
    async def test_compress_fresh_to_fermented_no_client(self, mock_types):
        """클라이언트 없이 압축"""
        result = await fermentation.compress_fresh_to_fermented(
            None, "model-id", [{"role": "user", "content": "test"}]
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_compress_fresh_to_fermented_success(self, sample_history):
        """성공적인 발효"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "<Compressed>요약된 내용</Compressed>"

        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        result = await fermentation.compress_fresh_to_fermented(
            mock_client, "model-id", sample_history
        )

        assert result is not None
        assert "요약된 내용" in result

    @pytest.mark.asyncio
    async def test_compress_fermented_to_deep_empty(self):
        """빈 FERMENTED 압축"""
        client = MagicMock()
        result = await fermentation.compress_fermented_to_deep(
            client, "model-id", []
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_compress_fermented_to_deep_success(self):
        """성공적인 DEEP 압축"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "장기 기억으로 압축된 내용"

        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        fermented_list = [
            {"timestamp": "t1", "summary": "첫 번째 세션 요약"},
            {"timestamp": "t2", "summary": "두 번째 세션 요약"}
        ]

        result = await fermentation.compress_fermented_to_deep(
            mock_client, "model-id", fermented_list
        )

        assert result is not None
        assert "장기 기억" in result


class TestAutoFerment:
    """자동 발효 테스트"""

    @pytest.mark.asyncio
    async def test_auto_ferment_no_action_needed(self):
        """발효 불필요"""
        session = {
            "history": [{"role": "user", "content": "test"}] * 10,
            "fermented_history": [],
            "deep_memory": ""
        }

        client = MagicMock()
        result = await fermentation.auto_ferment(client, "model-id", session)

        # 변경 없음
        assert len(result["history"]) == 10

    @pytest.mark.asyncio
    async def test_auto_ferment_fresh_trigger(self):
        """FRESH 발효 트리거"""
        # 임계값 초과 히스토리
        session = {
            "history": [{"role": "user", "content": f"msg{i}"} for i in range(60)],
            "fermented_history": [],
            "deep_memory": ""
        }

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "발효된 요약"
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        result = await fermentation.auto_ferment(mock_client, "model-id", session)

        # FERMENTED에 추가됨
        assert len(result["fermented_history"]) > 0
        # FRESH 줄어듦
        assert len(result["history"]) < 60


class TestForceFerment:
    """강제 발효 테스트"""

    @pytest.mark.asyncio
    async def test_force_ferment_insufficient_history(self):
        """히스토리 부족"""
        session = {
            "history": [{"role": "user", "content": "test"}] * 5,
            "fermented_history": []
        }

        client = MagicMock()
        success, msg = await fermentation.force_ferment(client, "model-id", session)

        assert success is False
        assert "부족" in msg

    @pytest.mark.asyncio
    async def test_force_ferment_success(self):
        """강제 발효 성공"""
        session = {
            "history": [{"role": "user", "content": f"msg{i}"} for i in range(20)],
            "fermented_history": []
        }

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "강제 발효된 요약"
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        success, msg = await fermentation.force_ferment(mock_client, "model-id", session)

        assert success is True
        assert "발효했습니다" in msg

    @pytest.mark.asyncio
    async def test_force_deep_compress_insufficient(self):
        """FERMENTED 부족"""
        session = {
            "fermented_history": [{"summary": "only one"}],
            "deep_memory": ""
        }

        client = MagicMock()
        success, msg = await fermentation.force_deep_compress(client, "model-id", session)

        assert success is False
        assert "부족" in msg

    @pytest.mark.asyncio
    async def test_force_deep_compress_success(self):
        """강제 DEEP 압축 성공"""
        session = {
            "fermented_history": [
                {"summary": "요약1"},
                {"summary": "요약2"},
                {"summary": "요약3"}
            ],
            "deep_memory": ""
        }

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "DEEP으로 압축된 내용"
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        success, msg = await fermentation.force_deep_compress(mock_client, "model-id", session)

        assert success is True
        assert "DEEP으로 압축" in msg


class TestConstants:
    """상수 테스트"""

    def test_ratio_sum(self):
        """비율 합계가 1.0"""
        total = fermentation.DEEP_RATIO + fermentation.FERMENTED_RATIO + fermentation.FRESH_RATIO
        assert total == 1.0

    def test_threshold_values(self):
        """임계값이 양수"""
        assert fermentation.FRESH_THRESHOLD > 0
        assert fermentation.FERMENT_CHUNK_SIZE > 0
        assert fermentation.FERMENTED_THRESHOLD > 0

    def test_prompts_not_empty(self):
        """프롬프트가 비어있지 않음"""
        assert len(fermentation.FERMENT_PROMPT) > 100
        assert len(fermentation.DEEP_COMPRESS_PROMPT) > 100


# Fixtures
@pytest.fixture
def sample_history():
    """샘플 히스토리"""
    return [
        {"role": "user", "content": "안녕하세요"},
        {"role": "assistant", "content": "안녕하세요! 무엇을 도와드릴까요?"},
        {"role": "user", "content": "TRPG 게임을 시작하고 싶어요"},
        {"role": "assistant", "content": "좋습니다! 캐릭터를 만들어볼까요?"}
    ]
