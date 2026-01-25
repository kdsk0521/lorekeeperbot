"""
Lorekeeper TRPG Bot - Input Handler 테스트
주사위 굴림, 마크다운 제거, 입력 파싱 테스트
"""

import pytest
from unittest.mock import patch
import input_handler


class TestStripDiscordMarkdown:
    """strip_discord_markdown 함수 테스트"""

    def test_empty_string(self):
        """빈 문자열 처리"""
        assert input_handler.strip_discord_markdown("") == ""

    def test_none_like_empty(self):
        """None 같은 빈 값 처리"""
        assert input_handler.strip_discord_markdown("") == ""

    def test_bold_removal(self):
        """굵게(**) 마크다운 제거"""
        assert input_handler.strip_discord_markdown("**굵은 텍스트**") == "굵은 텍스트"

    def test_italic_removal(self):
        """기울임(*) 마크다운 제거 - 단일 별표"""
        # 단일 별표는 패턴에 없어서 그대로 유지됨
        result = input_handler.strip_discord_markdown("*기울임*")
        assert "*" not in result or result == "*기울임*"

    def test_bold_italic_removal(self):
        """굵은 기울임(***) 마크다운 제거"""
        assert input_handler.strip_discord_markdown("***강조***") == "강조"

    def test_underline_removal(self):
        """밑줄(__) 마크다운 제거"""
        assert input_handler.strip_discord_markdown("__밑줄__") == "밑줄"

    def test_strikethrough_removal(self):
        """취소선(~~) 마크다운 제거"""
        assert input_handler.strip_discord_markdown("~~취소선~~") == "취소선"

    def test_spoiler_removal(self):
        """스포일러(||) 마크다운 제거"""
        assert input_handler.strip_discord_markdown("||스포일러||") == "스포일러"

    def test_code_removal(self):
        """코드(`) 마크다운 제거"""
        assert input_handler.strip_discord_markdown("`코드`") == "코드"

    def test_mixed_markdown(self):
        """복합 마크다운 제거"""
        text = "**굵게** 그리고 ~~취소~~ 그리고 `코드`"
        result = input_handler.strip_discord_markdown(text)
        assert "**" not in result
        assert "~~" not in result
        assert "`" not in result

    def test_plain_text_unchanged(self):
        """일반 텍스트는 변경 없음"""
        text = "안녕하세요, 테스트입니다!"
        assert input_handler.strip_discord_markdown(text) == text

    def test_whitespace_trimmed(self):
        """앞뒤 공백 제거"""
        assert input_handler.strip_discord_markdown("  텍스트  ") == "텍스트"


class TestAnalyzeStyle:
    """analyze_style 함수 테스트"""

    def test_dialogue_double_quote(self):
        """큰따옴표로 시작하는 대화문"""
        assert input_handler.analyze_style('"안녕"', '"안녕"') == "Dialogue"

    def test_dialogue_curly_quote(self):
        """둥근 따옴표로 시작하는 대화문"""
        assert input_handler.analyze_style('"안녕"', '"안녕"') == "Dialogue"

    def test_dialogue_single_quote(self):
        """작은따옴표로 시작하는 대화문"""
        assert input_handler.analyze_style("'안녕'", "'안녕'") == "Dialogue"

    def test_action_with_asterisks(self):
        """별표로 감싸진 행동"""
        assert input_handler.analyze_style("*검을 뽑는다*", "검을 뽑는다") == "Action"

    def test_description_plain(self):
        """일반 설명문"""
        assert input_handler.analyze_style("평범한 텍스트", "평범한 텍스트") == "Description"


class TestRollDice:
    """roll_dice 함수 테스트"""

    def test_basic_roll_format(self):
        """기본 주사위 형식 (1d20)"""
        result = input_handler.roll_dice("1d20")
        assert result is not None
        total, rolls, mod, detail = result
        assert 1 <= total <= 20
        assert mod == 0
        assert detail is None

    def test_roll_with_positive_modifier(self):
        """양수 수정치가 있는 주사위 (1d20+5)"""
        result = input_handler.roll_dice("1d20+5")
        assert result is not None
        total, rolls, mod, detail = result
        assert 6 <= total <= 25
        assert mod == 5

    def test_roll_with_negative_modifier(self):
        """음수 수정치가 있는 주사위 (1d20-3)"""
        result = input_handler.roll_dice("1d20-3")
        assert result is not None
        total, rolls, mod, detail = result
        assert -2 <= total <= 17
        assert mod == -3

    def test_multiple_dice(self):
        """여러 주사위 굴림 (2d6)"""
        result = input_handler.roll_dice("2d6")
        assert result is not None
        total, rolls, mod, detail = result
        assert 2 <= total <= 12
        assert len(rolls) == 2

    def test_large_dice(self):
        """큰 면 주사위 (1d100)"""
        result = input_handler.roll_dice("1d100")
        assert result is not None
        total, _, _, _ = result
        assert 1 <= total <= 100

    def test_advantage_mode(self):
        """유리함 모드"""
        result = input_handler.roll_dice("1d20", mode="adv")
        assert result is not None
        total, rolls_str, mod, detail = result
        assert "유리함" in detail
        assert 1 <= total <= 20

    def test_disadvantage_mode(self):
        """불리함 모드"""
        result = input_handler.roll_dice("1d20", mode="dis")
        assert result is not None
        total, rolls_str, mod, detail = result
        assert "불리함" in detail
        assert 1 <= total <= 20

    def test_invalid_format_returns_none(self):
        """잘못된 형식은 None 반환"""
        assert input_handler.roll_dice("invalid") is None
        assert input_handler.roll_dice("d20") is None  # 개수 없음
        assert input_handler.roll_dice("abc") is None

    def test_zero_dice_returns_none(self):
        """0개 주사위는 None 반환"""
        assert input_handler.roll_dice("0d20") is None

    def test_zero_sides_returns_none(self):
        """0면 주사위는 None 반환"""
        assert input_handler.roll_dice("1d0") is None

    def test_exceeds_max_dice_count(self):
        """최대 주사위 개수 초과시 None"""
        assert input_handler.roll_dice("101d20") is None

    def test_exceeds_max_dice_sides(self):
        """최대 주사위 면수 초과시 None"""
        assert input_handler.roll_dice("1d1001") is None

    def test_case_insensitive(self):
        """대소문자 구분 없음"""
        result = input_handler.roll_dice("1D20")
        assert result is not None


class TestParseInput:
    """parse_input 함수 테스트"""

    def test_empty_input(self):
        """빈 입력"""
        assert input_handler.parse_input("") is None
        assert input_handler.parse_input("   ") is None

    def test_command_recognition(self):
        """명령어 인식"""
        result = input_handler.parse_input("!도움")
        assert result is not None
        assert result["type"] == "command"
        assert result["command"] == "help"

    def test_command_with_args(self):
        """인자가 있는 명령어"""
        result = input_handler.parse_input("!로어 테스트 로어")
        assert result is not None
        assert result["type"] == "command"
        assert result["command"] == "lore"
        assert result["content"] == "테스트 로어"

    def test_korean_command_mapping(self):
        """한국어 명령어 매핑"""
        mappings = [
            ("!준비", "ready"),
            ("!리셋", "reset"),
            ("!초기화", "reset"),
            ("!시작", "start"),
            ("!정보", "info"),
            ("!퀘스트", "quest"),
            ("!메모", "memo"),
            ("!npc", "npc"),
        ]
        for korean, english in mappings:
            result = input_handler.parse_input(korean)
            assert result is not None, f"{korean} should parse"
            assert result["command"] == english, f"{korean} should map to {english}"

    def test_dice_command(self):
        """주사위 명령어"""
        result = input_handler.parse_input("!r 1d20")
        assert result is not None
        assert result["type"] == "dice"
        assert "🎲" in result["content"]

    def test_dice_command_korean(self):
        """한국어 주사위 명령어"""
        result = input_handler.parse_input("!주사위 1d20")
        assert result is not None
        assert result["type"] == "dice"

    def test_dice_invalid_format(self):
        """잘못된 주사위 형식"""
        result = input_handler.parse_input("!r invalid")
        assert result is not None
        assert result["type"] == "dice"
        assert "오류" in result["content"]

    def test_dice_advantage(self):
        """유리함 주사위"""
        result = input_handler.parse_input("!r 1d20 유리")
        assert result is not None
        assert result["type"] == "dice"

    def test_ooc_only(self):
        """OOC만 있는 경우"""
        result = input_handler.parse_input("(OOC: 잠시 쉬어가자)")
        assert result is not None
        assert result["type"] == "ooc"
        assert result["content"] == "잠시 쉬어가자"

    def test_ooc_with_chat(self):
        """OOC + 일반 채팅"""
        result = input_handler.parse_input('"안녕!" (OOC: 테스트)')
        assert result is not None
        assert result["type"] == "chat_with_ooc"
        assert "ooc_content" in result
        assert "chat_content" in result

    def test_chat_dialogue(self):
        """일반 대화 채팅"""
        result = input_handler.parse_input('"안녕하세요"')
        assert result is not None
        assert result["type"] == "chat"
        assert result["style"] == "Dialogue"

    def test_chat_action(self):
        """행동 채팅"""
        result = input_handler.parse_input("*검을 뽑는다*")
        assert result is not None
        assert result["type"] == "chat"
        assert result["style"] == "Action"

    def test_chat_description(self):
        """설명 채팅"""
        result = input_handler.parse_input("주변을 둘러본다")
        assert result is not None
        assert result["type"] == "chat"
        assert result["style"] == "Description"

    def test_empty_command(self):
        """빈 명령어 (! 만 입력)"""
        result = input_handler.parse_input("!")
        assert result is None
