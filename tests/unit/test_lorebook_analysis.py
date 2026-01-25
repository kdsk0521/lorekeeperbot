"""
Lorekeeper TRPG Bot - Lorebook 분석 테스트
실제 로어북 파일을 사용한 NPC 추출, 장르 분석, PC 정보 추출 테스트
"""

import pytest
import os
from unittest.mock import patch, AsyncMock, MagicMock

import memory_system


# 로어북 파일 경로
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), '..', 'fixtures')
LOREBOOK_PATH = os.path.join(FIXTURES_DIR, 'sample_lorebook.txt')


@pytest.fixture
def lorebook_text():
    """로어북 텍스트 픽스처"""
    with open(LOREBOOK_PATH, 'r', encoding='utf-8') as f:
        return f.read()


@pytest.fixture
def lorebook_npc_section():
    """NPC 관련 섹션만 추출"""
    with open(LOREBOOK_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # Key Characters 섹션 추출
    start_marker = "5. Key Characters"
    end_marker = "6. Base: The 3rd Clinic"

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)

    if start_idx != -1 and end_idx != -1:
        return content[start_idx:end_idx]
    return content[:5000]  # fallback


@pytest.fixture
def lorebook_pc_section():
    """PC 프로필 섹션만 추출"""
    with open(LOREBOOK_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # PC Profile 섹션 추출
    start_marker = "4. PC Profile: 아담"
    end_marker = "5. Key Characters"

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)

    if start_idx != -1 and end_idx != -1:
        return content[start_idx:end_idx]
    return ""


class TestLorebookLoading:
    """로어북 파일 로딩 테스트"""

    def test_lorebook_file_exists(self):
        """로어북 파일이 존재하는지 확인"""
        assert os.path.exists(LOREBOOK_PATH), "로어북 파일이 없습니다"

    def test_lorebook_not_empty(self, lorebook_text):
        """로어북이 비어있지 않은지 확인"""
        assert len(lorebook_text) > 1000, "로어북이 너무 짧습니다"

    def test_lorebook_contains_key_elements(self, lorebook_text):
        """로어북에 핵심 요소가 포함되어 있는지 확인"""
        # 세계관 이름
        assert "Suture City" in lorebook_text

        # 주요 NPC
        assert "클라라" in lorebook_text or "Clara" in lorebook_text
        assert "이브" in lorebook_text or "Eve" in lorebook_text

        # PC
        assert "아담" in lorebook_text or "Adam" in lorebook_text

        # 장르 키워드
        assert "Steam" in lorebook_text or "증기" in lorebook_text
        assert "Soul" in lorebook_text or "영혼" in lorebook_text


class TestGenreDetection:
    """장르 감지 테스트"""

    def test_genre_keywords_in_lorebook(self, lorebook_text):
        """로어북에서 장르 키워드 감지"""
        text_lower = lorebook_text.lower()

        # Steampunk 키워드
        steampunk_keywords = ["steam", "gear", "piston", "brass", "automaton"]
        steampunk_found = sum(1 for kw in steampunk_keywords if kw in text_lower)
        assert steampunk_found >= 3, f"Steampunk 키워드 {steampunk_found}개만 발견"

        # Post-apocalypse 키워드
        post_apoc_keywords = ["survival", "scavenge", "ruins", "collapse"]
        post_apoc_found = sum(1 for kw in post_apoc_keywords if kw in text_lower)
        assert post_apoc_found >= 2, f"Post-apocalypse 키워드 {post_apoc_found}개만 발견"

        # Occult 키워드
        occult_keywords = ["soul", "curse", "ritual", "blessing"]
        occult_found = sum(1 for kw in occult_keywords if kw in text_lower)
        assert occult_found >= 2, f"Occult 키워드 {occult_found}개만 발견"

    def test_supported_genres_match(self, lorebook_text):
        """지원되는 장르와 매칭되는지 확인"""
        text_lower = lorebook_text.lower()

        matched_genres = []
        for genre, keywords in memory_system.GENRE_KEYWORD_MAP.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    matched_genres.append(genre)
                    break

        # 최소 2개 이상의 장르가 매칭되어야 함
        assert len(matched_genres) >= 2, f"매칭된 장르: {matched_genres}"

        # Steampunk 또는 post_apocalypse가 포함되어야 함
        expected_genres = {"steampunk", "post_apocalypse", "occult"}
        assert len(set(matched_genres) & expected_genres) >= 1


class TestNPCExtraction:
    """NPC 추출 테스트 (API 모킹)"""

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_extract_npcs_from_lorebook(self, mock_api, lorebook_npc_section):
        """로어북에서 NPC 추출"""
        # 예상되는 NPC 목록 (실제 API 응답을 모킹)
        expected_response = '''[
            {"name": "클라라", "description": "아담의 자동인형 조수이자 경비원", "appearance": "빅토리안 스팀펑크 드레스를 간호복으로 개조, 도자기색 얼굴에 한쪽 볼이 갈라져 황동 기어가 보임"},
            {"name": "이브", "description": "아담의 미완성 프로젝트, 완벽한 인간을 만들려는 시도", "appearance": "여러 기증자의 피부로 패치워크된 아름답지만 무표정한 얼굴"}
        ]'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.extract_npcs_only(
            client, "model-id", lorebook_npc_section
        )

        # API가 호출되었는지 확인
        mock_api.assert_called_once()

        # 결과 검증
        assert len(result) == 2
        assert any(npc["name"] == "클라라" for npc in result)
        assert any(npc["name"] == "이브" for npc in result)

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_extract_npcs_excludes_pc(self, mock_api, lorebook_text):
        """PC(아담)가 NPC 목록에서 제외되는지 확인"""
        # 아담이 포함되지 않은 응답
        expected_response = '''[
            {"name": "클라라", "description": "자동인형 조수"},
            {"name": "이브", "description": "미완성 프로젝트"}
        ]'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.extract_npcs_only(
            client, "model-id", lorebook_text[:3000]
        )

        # 아담(PC)은 NPC 목록에 없어야 함
        npc_names = [npc.get("name", "") for npc in result]
        assert "아담" not in npc_names
        assert "Adam" not in npc_names


class TestPCExtraction:
    """PC 정보 추출 테스트 (API 모킹)"""

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_extract_pc_info(self, mock_api, lorebook_pc_section):
        """로어북에서 PC 정보 추출"""
        expected_response = '''{
            "name": "아담",
            "role": "무면허 외과의사, 정보 브로커, 영혼 계약 공증인",
            "appearance": "회색 머리, 선명한 녹색 눈, 피 묻은 흰 가운",
            "personality": "경직되고 냉소적이지만 자신만의 원칙이 있는",
            "passives": ["Godhand", "Anatomical Interrogation", "Pharmaceutical Mastery", "Soul Contract Notary"],
            "inventory": {"담배": 1, "사탕": 1, "소독약": 1}
        }'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.extract_pc_info(
            client, "model-id", lorebook_pc_section
        )

        assert result is not None
        assert result["name"] == "아담"
        assert "Godhand" in result["passives"]

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_extract_pc_modifications(self, mock_api, lorebook_pc_section):
        """PC의 신체 개조 정보 추출"""
        expected_response = '''{
            "name": "아담",
            "abilities": "왼팔 황동/백금 의수(독/마취제 주사기 내장), 다리 증기 피스톤(시속 40km 고속 이동)",
            "passives": ["Godhand", "Anatomical Interrogation"]
        }'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.extract_pc_info(
            client, "model-id", lorebook_pc_section
        )

        assert result is not None
        assert "abilities" in result


class TestGenreAnalysis:
    """장르 분석 테스트 (API 모킹)"""

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_analyze_genre(self, mock_api, lorebook_text):
        """로어북 장르 분석"""
        expected_response = '''{
            "genres": ["steampunk", "post_apocalypse", "occult"],
            "custom_tone": "영혼과 기억이 연료가 된 증기 문명에서의 생존을 그린 하드보일드 다크 판타지"
        }'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.analyze_genre_from_lore(
            client, "model-id", lorebook_text[:5000]
        )

        assert "steampunk" in result["genres"]
        assert result["custom_tone"] is not None

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_analyze_location_rules(self, mock_api, lorebook_text):
        """장소별 규칙 분석"""
        expected_response = '''{
            "Wet Market": "장기 거래와 영혼 증류소가 있는 위험한 시장",
            "Steam Cathedral": "영혼을 대량 연소하는 신성한 용광로",
            "Scrap Forest": "폐허에서 부품을 주워모으는 위험한 지역",
            "Ivory Corridor": "귀족들의 차가운 백색 구역",
            "Red Chimneys": "영혼강철을 제련하는 공장 지대"
        }'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.analyze_location_rules_from_lore(
            client, "model-id", lorebook_text[:5000]
        )

        assert len(result) >= 3


class TestWorldConstraints:
    """세계 규칙 추출 테스트"""

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_extract_world_constraints(self, mock_api, lorebook_text):
        """세계관 제약 사항 추출"""
        expected_response = '''{
            "setting": {"era": "포스트 아포칼립스 증기 시대", "location": "Suture City"},
            "theme": {"genres": ["steampunk", "occult"], "tone": "하드보일드 다크"},
            "systems": {
                "magic": "영혼과 기억 기반 초자연적 계약",
                "technology": "증기 기관, 자동인형, 신체 개조"
            },
            "social": {
                "taboos": ["총기 사용 불가"],
                "hierarchy": "무법 상태, 영혼 계약만이 유일한 신뢰 시스템"
            }
        }'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.extract_world_constraints(
            client, "model-id", lorebook_text[:5000]
        )

        assert "setting" in result
        assert "systems" in result

    def test_critical_world_rules_in_lorebook(self, lorebook_text):
        """로어북에 중요한 세계 규칙이 있는지 확인"""
        # 총기 금지 규칙
        assert "NO FIREARMS" in lorebook_text or "총기" in lorebook_text

        # 영혼 경제
        assert "SOUL" in lorebook_text.upper()

        # 무법 상태
        assert "NO LAW" in lorebook_text or "무법" in lorebook_text


class TestNarrativeConsistency:
    """내러티브 일관성 테스트"""

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_check_consistency(self, mock_api, lorebook_text):
        """내러티브 일관성 검사"""
        # 샘플 히스토리 (일관성 있는 경우)
        sample_history = '''
        [user] 아담이 환자의 왼팔을 검사한다.
        [assistant] 황동과 백금으로 이루어진 의수의 정교한 관절을 확인하며, 환자에게 "이 정도 수리면 영혼 10% 정도 들겠군" 하고 말한다.
        '''

        expected_response = '''{
            "overall_consistency": "High",
            "issues": [],
            "plot_threads": ["환자 치료", "영혼 거래"]
        }'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.check_narrative_consistency(
            client, "model-id", sample_history, lorebook_text[:3000]
        )

        assert result["overall_consistency"] == "High"
        assert len(result["issues"]) == 0


class TestBrainstorming:
    """브레인스토밍 테스트"""

    @pytest.mark.asyncio
    @patch('memory_system.api_call_with_retry')
    async def test_brainstorming_with_lore(self, mock_api, lorebook_text):
        """로어를 기반으로 한 브레인스토밍"""
        sample_history = "클라라가 수상한 손님을 발견했다."
        question = "이 손님은 어떤 목적으로 클리닉에 왔을까?"

        expected_response = '''{
            "current_state_summary": "아담의 제3클리닉에 수상한 손님이 도착함",
            "potential_paths": [
                {"path": "장기 매매를 위해", "pros": "돈벌이", "cons": "위험한 거래"},
                {"path": "정보를 얻기 위해", "pros": "새로운 플롯", "cons": "배신 가능성"},
                {"path": "이브를 훔치려고", "pros": "긴장감", "cons": "클라라와 전투"}
            ],
            "recommendation": "정보 브로커로서의 아담의 역할을 활용한 정보 거래 시나리오 추천",
            "open_questions": ["손님의 진짜 정체는?", "영혼 계약을 제안할 것인가?"]
        }'''
        mock_api.return_value = expected_response

        client = MagicMock()
        result = await memory_system.analyze_brainstorming(
            client, "model-id", sample_history, lorebook_text[:3000], question
        )

        assert "potential_paths" in result
        assert len(result["potential_paths"]) >= 2
