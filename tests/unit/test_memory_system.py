"""
Lorekeeper TRPG Bot - Memory System 테스트
JSON 파싱, 메모리 편집, 장르 분석 테스트
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import json

import memory_system


class TestSafeParseJson:
    """safe_parse_json 함수 테스트"""

    def test_empty_input(self):
        """빈 입력"""
        assert memory_system.safe_parse_json(None) == {}
        assert memory_system.safe_parse_json("") == {}

    def test_empty_input_expect_list(self):
        """빈 입력 (리스트 기대)"""
        assert memory_system.safe_parse_json(None, expect_list=True) == []
        assert memory_system.safe_parse_json("", expect_list=True) == []

    def test_valid_json_object(self):
        """유효한 JSON 객체"""
        result = memory_system.safe_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_valid_json_array(self):
        """유효한 JSON 배열"""
        result = memory_system.safe_parse_json('[{"name": "test"}]', expect_list=True)
        assert result == [{"name": "test"}]

    def test_json_in_markdown_code_block(self):
        """마크다운 코드 블록 안의 JSON"""
        text = '```json\n{"key": "value"}\n```'
        result = memory_system.safe_parse_json(text)
        assert result == {"key": "value"}

    def test_json_with_text_prefix(self):
        """텍스트가 앞에 있는 JSON"""
        text = 'Here is the result: {"key": "value"}'
        result = memory_system.safe_parse_json(text)
        assert result == {"key": "value"}

    def test_json_with_text_suffix(self):
        """텍스트가 뒤에 있는 JSON"""
        text = '{"key": "value"} This is the end.'
        result = memory_system.safe_parse_json(text)
        assert result == {"key": "value"}

    def test_nested_json(self):
        """중첩된 JSON"""
        text = '{"outer": {"inner": "value"}}'
        result = memory_system.safe_parse_json(text)
        assert result == {"outer": {"inner": "value"}}

    def test_json_array_to_dict(self):
        """배열을 딕셔너리로 변환 (기본 모드)"""
        text = '[{"name": "first"}, {"name": "second"}]'
        result = memory_system.safe_parse_json(text)  # expect_list=False
        # 첫 번째 요소 반환
        assert result == {"name": "first"}

    def test_dict_to_list(self):
        """딕셔너리를 리스트로 감싸기 (리스트 기대 모드)"""
        text = '{"name": "single"}'
        result = memory_system.safe_parse_json(text, expect_list=True)
        assert result == [{"name": "single"}]

    def test_invalid_json(self):
        """유효하지 않은 JSON"""
        result = memory_system.safe_parse_json('not a json')
        assert result == {}

    def test_malformed_json(self):
        """형식이 잘못된 JSON"""
        result = memory_system.safe_parse_json('{"key": "value"')  # 닫는 중괄호 누락
        assert result == {}

    def test_korean_content(self):
        """한국어 콘텐츠"""
        text = '{"이름": "테스트", "설명": "한국어 테스트"}'
        result = memory_system.safe_parse_json(text)
        assert result["이름"] == "테스트"
        assert result["설명"] == "한국어 테스트"


class TestApplyMemoryEdits:
    """apply_memory_edits 함수 테스트"""

    @pytest.fixture
    def base_ai_mem(self):
        """기본 AI 메모리"""
        return {
            "appearance": "긴 검은 머리",
            "personality": "조용함",
            "relationships": {"철수": "친구"},
            "passives": ["독 내성"],
        }

    @pytest.fixture
    def base_p_data(self):
        """기본 플레이어 데이터"""
        return {
            "inventory": {"검": 1, "포션": 3},
            "economy": {"gold": 100},
            "status_effects": ["부상"]
        }

    def test_set_appearance(self, base_ai_mem, base_p_data):
        """외모 설정"""
        edits = [{"field": "appearance", "action": "set", "value": "금발"}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_mem["appearance"] == "금발"

    def test_append_personality(self, base_ai_mem, base_p_data):
        """성격 추가"""
        edits = [{"field": "personality", "action": "append", "value": "그러나 열정적"}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert "조용함" in new_mem["personality"]
        assert "열정적" in new_mem["personality"]

    def test_update_relationship(self, base_ai_mem, base_p_data):
        """관계 업데이트"""
        edits = [{"field": "relationships", "action": "update", "key": "영희", "value": "연인"}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_mem["relationships"]["영희"] == "연인"
        assert new_mem["relationships"]["철수"] == "친구"  # 기존 유지

    def test_remove_relationship(self, base_ai_mem, base_p_data):
        """관계 제거"""
        edits = [{"field": "relationships", "action": "remove", "key": "철수"}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert "철수" not in new_mem["relationships"]

    def test_add_passive(self, base_ai_mem, base_p_data):
        """패시브 추가"""
        edits = [{"field": "passives", "action": "add", "value": "야간시야"}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert "야간시야" in new_mem["passives"]
        assert "독 내성" in new_mem["passives"]

    def test_remove_passive(self, base_ai_mem, base_p_data):
        """패시브 제거"""
        edits = [{"field": "passives", "action": "remove", "value": "독 내성"}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert "독 내성" not in new_mem["passives"]

    def test_add_inventory_item(self, base_ai_mem, base_p_data):
        """인벤토리 아이템 추가"""
        edits = [{"field": "inventory", "action": "add", "key": "방패", "value": 1}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_p["inventory"]["방패"] == 1

    def test_add_to_existing_inventory(self, base_ai_mem, base_p_data):
        """기존 인벤토리 아이템에 추가"""
        edits = [{"field": "inventory", "action": "add", "key": "포션", "value": 2}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_p["inventory"]["포션"] == 5

    def test_remove_inventory_item(self, base_ai_mem, base_p_data):
        """인벤토리 아이템 제거"""
        edits = [{"field": "inventory", "action": "remove", "key": "포션", "value": 1}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_p["inventory"]["포션"] == 2

    def test_remove_all_inventory_item(self, base_ai_mem, base_p_data):
        """모든 인벤토리 아이템 제거시 키 삭제"""
        edits = [{"field": "inventory", "action": "remove", "key": "검", "value": 1}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert "검" not in new_p["inventory"]

    def test_set_gold(self, base_ai_mem, base_p_data):
        """골드 설정"""
        edits = [{"field": "economy.gold", "action": "set", "value": 500}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_p["economy"]["gold"] == 500

    def test_add_gold(self, base_ai_mem, base_p_data):
        """골드 추가"""
        edits = [{"field": "economy.gold", "action": "add", "value": 50}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_p["economy"]["gold"] == 150

    def test_subtract_gold(self, base_ai_mem, base_p_data):
        """골드 차감"""
        edits = [{"field": "economy.gold", "action": "subtract", "value": 30}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_p["economy"]["gold"] == 70

    def test_subtract_gold_below_zero(self, base_ai_mem, base_p_data):
        """골드가 0 미만이 되지 않도록"""
        edits = [{"field": "economy.gold", "action": "subtract", "value": 200}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert new_p["economy"]["gold"] == 0

    def test_remove_status_effect(self, base_ai_mem, base_p_data):
        """상태 효과 제거"""
        edits = [{"field": "status_effects", "action": "remove", "value": "부상"}]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)
        assert "부상" not in new_p["status_effects"]

    def test_multiple_edits(self, base_ai_mem, base_p_data):
        """여러 편집 동시 적용"""
        edits = [
            {"field": "appearance", "action": "set", "value": "은발"},
            {"field": "inventory", "action": "add", "key": "활", "value": 1},
            {"field": "economy.gold", "action": "add", "value": 100},
            {"field": "passives", "action": "add", "value": "은신"}
        ]
        new_mem, new_p = memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)

        assert new_mem["appearance"] == "은발"
        assert new_p["inventory"]["활"] == 1
        assert new_p["economy"]["gold"] == 200
        assert "은신" in new_mem["passives"]

    def test_no_mutation_of_original(self, base_ai_mem, base_p_data):
        """원본 데이터가 변경되지 않음"""
        original_appearance = base_ai_mem["appearance"]
        original_gold = base_p_data["economy"]["gold"]

        edits = [
            {"field": "appearance", "action": "set", "value": "새로운 외모"},
            {"field": "economy.gold", "action": "set", "value": 9999}
        ]
        memory_system.apply_memory_edits(base_ai_mem, edits, base_p_data)

        assert base_ai_mem["appearance"] == original_appearance
        assert base_p_data["economy"]["gold"] == original_gold


class TestGenreKeywordMap:
    """GENRE_KEYWORD_MAP 상수 테스트"""

    def test_all_supported_genres_have_keywords(self):
        """모든 지원 장르에 키워드가 있음"""
        for genre in memory_system.SUPPORTED_GENRES:
            assert genre in memory_system.GENRE_KEYWORD_MAP, f"{genre} 장르에 키워드가 없음"

    def test_keywords_are_lists(self):
        """모든 키워드가 리스트 형태"""
        for genre, keywords in memory_system.GENRE_KEYWORD_MAP.items():
            assert isinstance(keywords, list), f"{genre} 키워드가 리스트가 아님"
            assert len(keywords) > 0, f"{genre} 키워드가 비어있음"

    def test_korean_keywords_exist(self):
        """한국어 키워드 포함 여부"""
        # 일부 장르는 한국어 키워드가 있어야 함
        korean_genres = ["wuxia", "high_fantasy", "cyberpunk"]
        for genre in korean_genres:
            keywords = memory_system.GENRE_KEYWORD_MAP[genre]
            has_korean = any(ord(char) > 0xAC00 for kw in keywords for char in kw)
            assert has_korean, f"{genre} 장르에 한국어 키워드가 없음"


class TestCognitiveArchitectureConstants:
    """인지 아키텍처 상수 테스트"""

    def test_cognitive_model_not_empty(self):
        """인지 모델이 비어있지 않음"""
        assert len(memory_system.COGNITIVE_ARCHITECTURE_MODEL) > 100

    def test_state_tracking_format_not_empty(self):
        """상태 추적 형식이 비어있지 않음"""
        assert len(memory_system.STATE_TRACKING_FORMAT) > 50

    def test_temporal_orientation_not_empty(self):
        """시간 방향 프로토콜이 비어있지 않음"""
        assert len(memory_system.TEMPORAL_ORIENTATION_PROTOCOL) > 50


class TestAsyncFunctions:
    """비동기 함수 테스트 (모킹 사용)"""

    @pytest.mark.asyncio
    async def test_extract_npcs_empty_text(self):
        """빈 텍스트로 NPC 추출"""
        client = MagicMock()
        result = await memory_system.extract_npcs_only(client, "model-id", "")
        assert result == []

    @pytest.mark.asyncio
    async def test_analyze_genre_empty_text(self):
        """빈 텍스트로 장르 분석"""
        client = MagicMock()
        result = await memory_system.analyze_genre_from_lore(client, "model-id", "")
        assert result["genres"] == ["noir"]

    @pytest.mark.asyncio
    async def test_extract_pc_info_empty_text(self):
        """빈 텍스트로 PC 정보 추출"""
        client = MagicMock()
        result = await memory_system.extract_pc_info(client, "model-id", "")
        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_brainstorming_empty_question(self):
        """빈 질문으로 브레인스토밍"""
        client = MagicMock()
        result = await memory_system.analyze_brainstorming(client, "model-id", "", "", "")
        assert result["analysis_type"] == "error"

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_extract_npcs_with_mock(self, mock_api):
        """NPC 추출 (API 모킹)"""
        mock_api.return_value = '[{"name": "테스트NPC", "description": "테스트 설명"}]'

        client = MagicMock()
        result = await memory_system.extract_npcs_only(
            client, "model-id", "테스트 로어 텍스트"
        )

        assert len(result) == 1
        assert result[0]["name"] == "테스트NPC"

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_analyze_genre_with_mock(self, mock_api):
        """장르 분석 (API 모킹)"""
        mock_api.return_value = '{"genres": ["high_fantasy", "wuxia"], "custom_tone": "웅장한 분위기"}'

        client = MagicMock()
        result = await memory_system.analyze_genre_from_lore(
            client, "model-id", "용과 마법사가 등장하는 이야기"
        )

        assert "high_fantasy" in result["genres"]
        assert result["custom_tone"] == "웅장한 분위기"
