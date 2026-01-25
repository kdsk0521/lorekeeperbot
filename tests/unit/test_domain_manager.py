"""
Lorekeeper TRPG Bot - Domain Manager 테스트
파일 I/O, 캐싱, 세션 관리 테스트
"""

import pytest
import os
import json
import tempfile
from unittest.mock import patch, MagicMock

import domain_manager
import config


class TestFilePaths:
    """파일 경로 생성 테스트"""

    def test_get_session_file_path(self):
        """세션 파일 경로"""
        path = domain_manager.get_session_file_path("123456")
        assert "123456.json" in path
        assert config.SESSIONS_DIR in path

    def test_get_lore_file_path(self):
        """로어 파일 경로"""
        path = domain_manager.get_lore_file_path("123456")
        assert "123456.txt" in path
        assert config.LORE_DIR in path

    def test_get_lore_original_file_path(self):
        """원본 로어 파일 경로"""
        path = domain_manager.get_lore_original_file_path("123456")
        assert "123456_original.txt" in path

    def test_get_rules_file_path(self):
        """규칙 파일 경로"""
        path = domain_manager.get_rules_file_path("123456")
        assert "123456.txt" in path
        assert config.RULES_DIR in path


class TestDefaultSession:
    """기본 세션 데이터 테스트"""

    def test_default_session_structure(self):
        """기본 세션 구조"""
        default = domain_manager._get_default_session()

        # 필수 키 확인
        assert "participants" in default
        assert "npcs" in default
        assert "history" in default
        assert "quest_board" in default
        assert "world_state" in default
        assert "settings" in default
        assert "fermented_history" in default
        assert "deep_memory" in default

    def test_default_session_types(self):
        """기본 세션 타입"""
        default = domain_manager._get_default_session()

        assert isinstance(default["participants"], dict)
        assert isinstance(default["history"], list)
        assert isinstance(default["fermented_history"], list)
        assert isinstance(default["deep_memory"], str)

    def test_default_quest_board(self):
        """기본 퀘스트 보드"""
        default = domain_manager._get_default_session()
        quest_board = default["quest_board"]

        assert "active" in quest_board
        assert "completed" in quest_board
        assert "memos" in quest_board


class TestDefaultParticipant:
    """기본 참가자 데이터 테스트"""

    def test_default_participant_structure(self):
        """기본 참가자 구조"""
        participant = domain_manager._create_default_participant("테스트유저")

        assert participant["mask"] == "테스트유저"
        assert participant["status"] == "active"
        assert "economy" in participant
        assert "inventory" in participant
        assert "ai_memory" in participant

    def test_default_participant_ai_memory(self):
        """기본 참가자 AI 메모리"""
        participant = domain_manager._create_default_participant("유저")
        ai_mem = participant["ai_memory"]

        assert "appearance" in ai_mem
        assert "personality" in ai_mem
        assert "relationships" in ai_mem
        assert "passives" in ai_mem


class TestFileIO:
    """파일 I/O 테스트 (임시 디렉토리 사용)"""

    def test_load_json_not_exists(self, temp_dir):
        """존재하지 않는 JSON 파일"""
        path = os.path.join(temp_dir, "nonexistent.json")
        result = domain_manager.load_json(path, {"default": True})
        assert result == {"default": True}

    def test_save_and_load_json(self, temp_dir):
        """JSON 저장 및 로드"""
        path = os.path.join(temp_dir, "test.json")
        data = {"key": "value", "number": 42, "korean": "한글"}

        success = domain_manager.save_json(path, data)
        assert success is True

        loaded = domain_manager.load_json(path, {})
        assert loaded["key"] == "value"
        assert loaded["number"] == 42
        assert loaded["korean"] == "한글"

    def test_load_text_not_exists(self, temp_dir):
        """존재하지 않는 텍스트 파일"""
        path = os.path.join(temp_dir, "nonexistent.txt")
        result = domain_manager.load_text(path, "default text")
        assert result == "default text"

    def test_save_and_load_text(self, temp_dir):
        """텍스트 저장 및 로드"""
        path = os.path.join(temp_dir, "test.txt")
        text = "테스트 텍스트\n여러 줄\n포함"

        success = domain_manager.save_text(path, text)
        assert success is True

        loaded = domain_manager.load_text(path, "")
        assert loaded == text


class TestSessionCaching:
    """세션 캐싱 테스트"""

    def test_cache_population(self, temp_session_dir):
        """캐시 채우기"""
        channel_id = "test_cache_123"

        # 캐시 비우기
        domain_manager._session_cache.pop(channel_id, None)

        # 첫 번째 호출 - 파일에서 로드 또는 기본값
        data1 = domain_manager.get_domain(channel_id)

        # 두 번째 호출 - 캐시에서 가져옴
        data2 = domain_manager.get_domain(channel_id)

        # 같은 객체여야 함 (캐시됨)
        assert data1 is data2

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_cache_invalidation_on_reset(self, temp_session_dir):
        """리셋 시 캐시 무효화"""
        channel_id = "test_reset_456"

        # 데이터 생성
        domain_manager.get_domain(channel_id)
        assert channel_id in domain_manager._session_cache

        # 리셋
        domain_manager.reset_domain(channel_id)

        # 캐시에서 제거됨
        assert channel_id not in domain_manager._session_cache


class TestNPCManagement:
    """NPC 관리 테스트"""

    def test_get_npcs_empty(self, temp_session_dir):
        """빈 NPC 목록"""
        channel_id = "test_npc_empty"
        domain_manager._session_cache.pop(channel_id, None)

        npcs = domain_manager.get_npcs(channel_id)
        assert isinstance(npcs, dict)

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_update_and_get_npc(self, temp_session_dir):
        """NPC 업데이트 및 조회"""
        channel_id = "test_npc_update"
        domain_manager._session_cache.pop(channel_id, None)

        # NPC 추가
        domain_manager.update_npc(channel_id, "클라라", {
            "desc": "자동인형 조수",
            "status": "Active"
        })

        # 조회
        npc = domain_manager.get_npc(channel_id, "클라라")
        assert npc is not None
        assert npc["desc"] == "자동인형 조수"

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_delete_npc(self, temp_session_dir):
        """NPC 삭제"""
        channel_id = "test_npc_delete"
        domain_manager._session_cache.pop(channel_id, None)

        # NPC 추가
        domain_manager.update_npc(channel_id, "임시NPC", {"desc": "테스트"})

        # 삭제
        result = domain_manager.delete_npc(channel_id, "임시NPC")
        assert result is True

        # 확인
        npc = domain_manager.get_npc(channel_id, "임시NPC")
        assert npc is None

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_delete_nonexistent_npc(self, temp_session_dir):
        """없는 NPC 삭제"""
        channel_id = "test_npc_delete_none"
        domain_manager._session_cache.pop(channel_id, None)

        result = domain_manager.delete_npc(channel_id, "없는NPC")
        assert result is False

        # 정리
        domain_manager._session_cache.pop(channel_id, None)


class TestLoreManagement:
    """로어 관리 테스트"""

    def test_get_lore_default(self, temp_session_dir):
        """기본 로어 가져오기"""
        channel_id = "test_lore_default"
        domain_manager._lore_cache.pop(channel_id, None)

        lore = domain_manager.get_lore(channel_id)
        assert lore == config.DEFAULT_LORE

        # 정리
        domain_manager._lore_cache.pop(channel_id, None)

    def test_append_lore(self, temp_session_dir):
        """로어 추가"""
        channel_id = "test_lore_append"
        domain_manager._lore_cache.pop(channel_id, None)

        # 첫 번째 추가 (기본값 대체)
        domain_manager.append_lore(channel_id, "새로운 로어 내용")

        lore = domain_manager.get_lore(channel_id)
        assert "새로운 로어 내용" in lore

        # 정리
        domain_manager._lore_cache.pop(channel_id, None)

    def test_get_lore_with_npcs(self, temp_session_dir):
        """NPC 포함 로어 가져오기"""
        channel_id = "test_lore_with_npcs"
        domain_manager._session_cache.pop(channel_id, None)
        domain_manager._lore_cache.pop(channel_id, None)

        # NPC 추가
        domain_manager.update_npc(channel_id, "테스트NPC", {
            "desc": "NPC 설명",
            "status": "Active"
        })

        lore = domain_manager.get_lore_with_npcs(channel_id)
        assert "테스트NPC" in lore
        assert "NPC 설명" in lore

        # 정리
        domain_manager._session_cache.pop(channel_id, None)
        domain_manager._lore_cache.pop(channel_id, None)


class TestGenreManagement:
    """장르 관리 테스트"""

    def test_get_active_genres_default(self, temp_session_dir):
        """기본 장르"""
        channel_id = "test_genre_default"
        domain_manager._session_cache.pop(channel_id, None)

        genres = domain_manager.get_active_genres(channel_id)
        assert "noir" in genres

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_set_active_genres(self, temp_session_dir):
        """장르 설정"""
        channel_id = "test_genre_set"
        domain_manager._session_cache.pop(channel_id, None)

        domain_manager.set_active_genres(channel_id, ["steampunk", "occult"])

        genres = domain_manager.get_active_genres(channel_id)
        assert "steampunk" in genres
        assert "occult" in genres

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_custom_tone(self, temp_session_dir):
        """커스텀 톤"""
        channel_id = "test_tone"
        domain_manager._session_cache.pop(channel_id, None)

        # 기본값
        tone = domain_manager.get_custom_tone(channel_id)
        assert tone is None

        # 설정
        domain_manager.set_custom_tone(channel_id, "하드보일드 다크 판타지")

        tone = domain_manager.get_custom_tone(channel_id)
        assert tone == "하드보일드 다크 판타지"

        # 정리
        domain_manager._session_cache.pop(channel_id, None)


class TestParticipantManagement:
    """참가자 관리 테스트"""

    def test_update_participant_new(self, temp_session_dir):
        """새 참가자 추가"""
        channel_id = "test_participant_new"
        domain_manager._session_cache.pop(channel_id, None)

        mock_user = MagicMock()
        mock_user.id = 12345
        mock_user.display_name = "테스트유저"

        result = domain_manager.update_participant(channel_id, mock_user)
        assert result is True

        data = domain_manager.get_participant_data(channel_id, "12345")
        assert data is not None
        assert data["mask"] == "테스트유저"

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_update_participant_reset(self, temp_session_dir):
        """참가자 리셋"""
        channel_id = "test_participant_reset"
        domain_manager._session_cache.pop(channel_id, None)

        mock_user = MagicMock()
        mock_user.id = 12345
        mock_user.display_name = "유저"

        # 첫 번째 등록
        domain_manager.update_participant(channel_id, mock_user)

        # 데이터 변경
        d = domain_manager.get_domain(channel_id)
        d["participants"]["12345"]["inventory"]["검"] = 1
        domain_manager.save_domain(channel_id, d)

        # 리셋
        mock_user.display_name = "새이름"
        domain_manager.update_participant(channel_id, mock_user, reset=True)

        data = domain_manager.get_participant_data(channel_id, "12345")
        assert data["mask"] == "새이름"
        assert data["inventory"] == {}  # 리셋됨

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_get_participant_data_not_exists(self, temp_session_dir):
        """없는 참가자"""
        channel_id = "test_participant_none"
        domain_manager._session_cache.pop(channel_id, None)

        data = domain_manager.get_participant_data(channel_id, "nonexistent")
        assert data is None

        # 정리
        domain_manager._session_cache.pop(channel_id, None)


class TestExportIndices:
    """내보내기 인덱스 테스트"""

    def test_export_idx(self, temp_session_dir):
        """내보내기 인덱스"""
        channel_id = "test_export_idx"
        domain_manager._session_cache.pop(channel_id, None)

        # 기본값
        idx = domain_manager.get_last_export_idx(channel_id)
        assert idx == 0

        # 설정
        domain_manager.set_last_export_idx(channel_id, 42)

        idx = domain_manager.get_last_export_idx(channel_id)
        assert idx == 42

        # 정리
        domain_manager._session_cache.pop(channel_id, None)

    def test_chronicle_idx(self, temp_session_dir):
        """연대기 인덱스"""
        channel_id = "test_chronicle_idx"
        domain_manager._session_cache.pop(channel_id, None)

        # 기본값
        idx = domain_manager.get_last_chronicle_idx(channel_id)
        assert idx == 0

        # 설정
        domain_manager.set_last_chronicle_idx(channel_id, 100)

        idx = domain_manager.get_last_chronicle_idx(channel_id)
        assert idx == 100

        # 정리
        domain_manager._session_cache.pop(channel_id, None)


class TestRulesManagement:
    """규칙 관리 테스트"""

    def test_get_rules_default(self, temp_session_dir):
        """기본 규칙"""
        channel_id = "test_rules_default"
        domain_manager._rules_cache.pop(channel_id, None)

        rules = domain_manager.get_rules(channel_id)
        assert rules == config.DEFAULT_RULES

        # 정리
        domain_manager._rules_cache.pop(channel_id, None)

    def test_rules_mode(self, temp_session_dir):
        """규칙 모드"""
        channel_id = "test_rules_mode"
        domain_manager._session_cache.pop(channel_id, None)

        mode = domain_manager.get_rules_mode(channel_id)
        assert mode == "default"

        # 정리
        domain_manager._session_cache.pop(channel_id, None)


# Fixtures
@pytest.fixture
def temp_dir():
    """임시 디렉토리"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def temp_session_dir(temp_dir):
    """임시 세션 디렉토리 (config 패치)"""
    sessions_dir = os.path.join(temp_dir, "sessions")
    lore_dir = os.path.join(temp_dir, "lores")
    rules_dir = os.path.join(temp_dir, "rules")

    os.makedirs(sessions_dir, exist_ok=True)
    os.makedirs(lore_dir, exist_ok=True)
    os.makedirs(rules_dir, exist_ok=True)

    with patch.object(config, 'SESSIONS_DIR', sessions_dir), \
         patch.object(config, 'LORE_DIR', lore_dir), \
         patch.object(config, 'RULES_DIR', rules_dir):
        yield temp_dir
