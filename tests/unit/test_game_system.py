"""
Lorekeeper TRPG Bot - Game System 테스트
둠 시스템, 인벤토리, 상태 효과, 비일상 적응 테스트
"""

import pytest
from unittest.mock import patch, MagicMock
import game_system
import config


class TestDoomDescription:
    """_get_doom_description 함수 테스트"""

    def test_doom_max(self):
        """최대 둠 수치"""
        result = game_system._get_doom_description(100)
        assert "파멸" in result

    def test_doom_critical(self):
        """치명적 둠 수치 (90+)"""
        result = game_system._get_doom_description(90)
        assert "절망" in result

    def test_doom_danger(self):
        """위험 둠 수치 (70+)"""
        result = game_system._get_doom_description(70)
        assert "위협" in result

    def test_doom_warning(self):
        """경고 둠 수치 (30+)"""
        result = game_system._get_doom_description(30)
        assert "불길" in result

    def test_doom_safe(self):
        """안전 둠 수치 (30 미만)"""
        result = game_system._get_doom_description(10)
        assert "평온" in result

    def test_doom_zero(self):
        """둠 0"""
        result = game_system._get_doom_description(0)
        assert "평온" in result


class TestRandomDoomEvent:
    """get_random_doom_event 함수 테스트"""

    def test_critical_doom_event(self):
        """치명적 둠 이벤트"""
        result = game_system.get_random_doom_event(95)
        assert "둠 이벤트" in result
        # 최소한 이모지가 포함되어야 함
        assert any(emoji in result for emoji in ["🌌", "👁️", "🩸", "🌑"])

    def test_danger_doom_event(self):
        """위험 둠 이벤트"""
        result = game_system.get_random_doom_event(75)
        assert "둠 이벤트" in result

    def test_warning_doom_event(self):
        """경고 둠 이벤트"""
        result = game_system.get_random_doom_event(40)
        assert "둠 이벤트" in result

    def test_calm_doom_event(self):
        """평온 둠 이벤트"""
        result = game_system.get_random_doom_event(10)
        assert "둠 이벤트" in result
        # 평화로운 이벤트 이모지
        assert any(emoji in result for emoji in ["🌸", "🐦", "☀️", "🏠"])


class TestInventory:
    """update_inventory 함수 테스트"""

    def test_add_new_item(self, sample_user_data):
        """새 아이템 추가"""
        updated, msg = game_system.update_inventory(sample_user_data, "add", "검", 1)
        assert updated["inventory"]["검"] == 1
        assert "획득" in msg

    def test_add_multiple_items(self, sample_user_data):
        """여러 개 아이템 추가"""
        updated, msg = game_system.update_inventory(sample_user_data, "add", "포션", 5)
        assert updated["inventory"]["포션"] == 5

    def test_add_to_existing_item(self, sample_user_data_with_effects):
        """기존 아이템에 추가"""
        # 이미 포션 3개 있음
        updated, msg = game_system.update_inventory(sample_user_data_with_effects, "add", "포션", 2)
        assert updated["inventory"]["포션"] == 5

    def test_remove_item(self, sample_user_data_with_effects):
        """아이템 제거"""
        updated, msg = game_system.update_inventory(sample_user_data_with_effects, "remove", "포션", 1)
        assert updated["inventory"]["포션"] == 2
        assert "사용" in msg

    def test_remove_all_items(self, sample_user_data_with_effects):
        """모든 아이템 제거시 키 삭제"""
        updated, msg = game_system.update_inventory(sample_user_data_with_effects, "remove", "포션", 3)
        assert "포션" not in updated["inventory"]

    def test_remove_insufficient(self, sample_user_data_with_effects):
        """부족한 아이템 제거 시도"""
        updated, msg = game_system.update_inventory(sample_user_data_with_effects, "remove", "포션", 10)
        assert "부족" in msg
        # 인벤토리는 변경되지 않아야 함
        assert updated["inventory"]["포션"] == 3

    def test_remove_nonexistent_item(self, sample_user_data):
        """없는 아이템 제거 시도"""
        updated, msg = game_system.update_inventory(sample_user_data, "remove", "없는아이템", 1)
        assert "부족" in msg


class TestStatusEffects:
    """update_status_effect 함수 테스트"""

    def test_add_debuff(self, sample_user_data):
        """디버프 추가"""
        updated, msg = game_system.update_status_effect(sample_user_data, "add", "부상")
        assert "부상" in updated["status_effects"]
        assert "⚠️" in msg

    def test_add_buff(self, sample_user_data):
        """버프 추가"""
        updated, msg = game_system.update_status_effect(sample_user_data, "add", "활력")
        assert "활력" in updated["status_effects"]
        assert "✨" in msg

    def test_add_duplicate_effect(self, sample_user_data_with_effects):
        """이미 있는 효과 추가"""
        updated, msg = game_system.update_status_effect(sample_user_data_with_effects, "add", "부상")
        assert "이미" in msg
        # 중복 추가되면 안 됨
        assert updated["status_effects"].count("부상") == 1

    def test_remove_effect(self, sample_user_data_with_effects):
        """효과 제거"""
        updated, msg = game_system.update_status_effect(sample_user_data_with_effects, "remove", "부상")
        assert "부상" not in updated["status_effects"]
        assert "해제" in msg

    def test_remove_nonexistent_effect(self, sample_user_data):
        """없는 효과 제거"""
        updated, msg = game_system.update_status_effect(sample_user_data, "remove", "없는효과")
        assert "없음" in msg


class TestNormalityCalculation:
    """calculate_normality 함수 테스트"""

    def test_zero_count(self):
        """노출 0회"""
        assert game_system.calculate_normality(0) == 0

    def test_first_exposure(self):
        """첫 노출"""
        result = game_system.calculate_normality(1)
        assert 0 < result < 50  # 첫 노출은 낮은 적응도

    def test_multiple_exposures(self):
        """여러 번 노출"""
        result_3 = game_system.calculate_normality(3)
        result_5 = game_system.calculate_normality(5)
        result_10 = game_system.calculate_normality(10)

        # 노출 횟수가 많을수록 적응도가 높아야 함
        assert result_3 < result_5 < result_10

    def test_max_normality(self):
        """최대 적응도 100 제한"""
        result = game_system.calculate_normality(100)
        assert result == 100


class TestNormalityStage:
    """get_normality_stage 및 get_normality_stage_info 함수 테스트"""

    def test_stage_0_shock(self):
        """0단계 (충격)"""
        stage = game_system.get_normality_stage(5)
        assert stage["stage"] == 0
        assert "초기" in stage["name"] or "Shock" in stage["name"]

    def test_stage_1_contact(self):
        """1단계 (접촉)"""
        stage = game_system.get_normality_stage(20)
        assert stage["stage"] == 1

    def test_stage_2_coping(self):
        """2단계 (적응)"""
        stage = game_system.get_normality_stage(45)
        assert stage["stage"] == 2

    def test_stage_3_routine(self):
        """3단계 (익숙함)"""
        stage = game_system.get_normality_stage(75)
        assert stage["stage"] == 3

    def test_stage_4_normal(self):
        """4단계 (일상화)"""
        stage = game_system.get_normality_stage(95)
        assert stage["stage"] == 4


class TestExposeToAbnormal:
    """expose_to_abnormal 함수 테스트"""

    def test_first_exposure(self, sample_user_data):
        """첫 비일상 노출"""
        updated, msg, stage = game_system.expose_to_abnormal(sample_user_data, "언데드", 1)

        assert "언데드" in updated["abnormal_exposure"]
        assert updated["abnormal_exposure"]["언데드"]["count"] == 1
        # 첫 노출시 메시지는 None일 수 있음 (단계 변경 없으면)

    def test_repeated_exposure(self, sample_user_data):
        """반복 노출"""
        # 여러 번 노출
        for i in range(5):
            updated, msg, stage = game_system.expose_to_abnormal(sample_user_data, "언데드", 1)

        assert updated["abnormal_exposure"]["언데드"]["count"] == 5
        assert updated["abnormal_exposure"]["언데드"]["normality"] > 0

    def test_stage_change_notification(self, sample_user_data):
        """단계 변경 시 알림"""
        # 여러 번 노출하여 단계 변경 유도
        for i in range(10):
            updated, msg, stage = game_system.expose_to_abnormal(sample_user_data, "뱀파이어", 1)

        # 어느 시점에서는 단계 변경 메시지가 있어야 함
        # (이 테스트는 로직에 따라 조정 필요)

    def test_multiple_abnormal_types(self, sample_user_data):
        """여러 종류의 비일상"""
        game_system.expose_to_abnormal(sample_user_data, "언데드", 1)
        game_system.expose_to_abnormal(sample_user_data, "마법", 1)

        assert "언데드" in sample_user_data["abnormal_exposure"]
        assert "마법" in sample_user_data["abnormal_exposure"]


class TestAbnormalContext:
    """get_abnormal_context 함수 테스트"""

    def test_empty_exposure(self, sample_user_data):
        """노출 기록 없을 때"""
        result = game_system.get_abnormal_context(sample_user_data, [])
        assert result == ""

    def test_with_exposure_history(self, sample_user_data_with_effects):
        """노출 기록 있을 때"""
        result = game_system.get_abnormal_context(sample_user_data_with_effects, ["언데드"])
        assert "언데드" in result
        assert "60%" in result  # 픽스처에서 설정한 normality

    def test_new_abnormal_type(self, sample_user_data_with_effects):
        """새로운 비일상 타입"""
        result = game_system.get_abnormal_context(sample_user_data_with_effects, ["드래곤"])
        assert "드래곤" in result
        assert "0%" in result or "New" in result


class TestStatusDoomModifier:
    """get_status_doom_modifier 함수 테스트"""

    def test_empty_effects(self):
        """효과 없을 때"""
        inc, dec, neg, pos = game_system.get_status_doom_modifier([])
        assert inc == 0
        assert dec == 0

    def test_negative_effects(self):
        """부정 효과"""
        inc, dec, neg, pos = game_system.get_status_doom_modifier(["부상", "중독"])
        assert inc > 0
        assert len(neg) > 0

    def test_positive_effects(self):
        """긍정 효과"""
        inc, dec, neg, pos = game_system.get_status_doom_modifier(["활력", "집중"])
        assert dec > 0
        assert len(pos) > 0

    def test_mixed_effects(self):
        """혼합 효과"""
        inc, dec, neg, pos = game_system.get_status_doom_modifier(["부상", "활력"])
        assert inc > 0
        assert dec > 0


class TestStatusSummary:
    """get_status_summary 함수 테스트"""

    def test_normal_status(self, sample_user_data):
        """정상 상태"""
        result = game_system.get_status_summary(sample_user_data)
        assert "정상" in result

    def test_with_effects(self, sample_user_data_with_effects):
        """효과 있는 상태"""
        result = game_system.get_status_summary(sample_user_data_with_effects)
        assert "부상" in result or "중독" in result

    def test_with_inventory(self, sample_user_data_with_effects):
        """인벤토리 포함"""
        result = game_system.get_status_summary(sample_user_data_with_effects)
        assert "소지품" in result


class TestQuestOperations:
    """퀘스트/메모 관련 테스트 (mock 사용)"""

    @patch('game_system.domain_manager')
    def test_add_quest(self, mock_domain):
        """퀘스트 추가"""
        mock_domain.get_domain.return_value = {"quest_board": {"active": [], "completed": [], "memos": []}}

        result = game_system.add_quest("test_channel", "테스트 퀘스트")
        assert result is not None
        assert "등록" in result

    @patch('game_system.domain_manager')
    def test_add_memo(self, mock_domain):
        """메모 추가"""
        mock_domain.get_domain.return_value = {"quest_board": {"active": [], "completed": [], "memos": []}}

        result = game_system.add_memo("test_channel", "테스트 메모")
        assert result is not None
        assert "등록" in result

    @patch('game_system.domain_manager')
    def test_add_duplicate_quest(self, mock_domain):
        """중복 퀘스트 추가"""
        mock_domain.get_domain.return_value = {"quest_board": {"active": ["기존 퀘스트"], "completed": [], "memos": []}}

        result = game_system.add_quest("test_channel", "기존 퀘스트")
        assert "이미" in result

    @patch('game_system.domain_manager')
    def test_get_active_quests_empty(self, mock_domain):
        """빈 퀘스트 목록"""
        mock_domain.get_domain.return_value = {"quest_board": {"active": [], "completed": [], "memos": []}}

        result = game_system.get_active_quests_text("test_channel")
        assert "없습니다" in result

    @patch('game_system.domain_manager')
    def test_get_active_quests_with_items(self, mock_domain):
        """퀘스트 있는 목록"""
        mock_domain.get_domain.return_value = {"quest_board": {"active": ["퀘스트1", "퀘스트2"], "completed": [], "memos": []}}

        result = game_system.get_active_quests_text("test_channel")
        assert "퀘스트1" in result
        assert "퀘스트2" in result


class TestPassivesContext:
    """get_passives_for_context 함수 테스트"""

    def test_no_passives(self, sample_user_data):
        """패시브 없음"""
        result = game_system.get_passives_for_context(sample_user_data)
        assert "None" in result

    def test_with_passives(self, sample_user_data_with_effects):
        """패시브 있음"""
        result = game_system.get_passives_for_context(sample_user_data_with_effects)
        assert "독 내성" in result

    def test_passives_from_ai_memory(self):
        """AI 메모리에서 온 패시브"""
        user_data = {
            "passives": [],
            "ai_memory": {"passives": ["은신술", "야간시야"]}
        }
        result = game_system.get_passives_for_context(user_data)
        assert "은신술" in result
        assert "야간시야" in result
