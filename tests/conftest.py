"""
Lorekeeper TRPG Bot - Test Configuration
공통 픽스처 및 테스트 설정
"""

import pytest
import sys
import os
from unittest.mock import MagicMock

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# google.genai 모듈 모킹 (설치되지 않은 경우 대비)
mock_genai = MagicMock()
mock_genai.types = MagicMock()
mock_genai.types.Content = MagicMock()
mock_genai.types.Part = MagicMock()
mock_genai.types.GenerateContentConfig = MagicMock()
sys.modules['google'] = MagicMock()
sys.modules['google.genai'] = mock_genai
sys.modules['google.genai.types'] = mock_genai.types
sys.modules['google.api_core'] = MagicMock()
sys.modules['google.api_core.exceptions'] = MagicMock()


@pytest.fixture
def sample_user_data():
    """기본 유저 데이터 픽스처"""
    return {
        "name": "테스트 캐릭터",
        "status_effects": [],
        "inventory": {},
        "passives": [],
        "abnormal_exposure": {},
        "ai_memory": {}
    }


@pytest.fixture
def sample_user_data_with_effects():
    """상태 효과가 있는 유저 데이터 픽스처"""
    return {
        "name": "부상당한 캐릭터",
        "status_effects": ["부상", "중독"],
        "inventory": {"검": 1, "포션": 3},
        "passives": [{"name": "독 내성"}],
        "abnormal_exposure": {
            "언데드": {"count": 5, "normality": 60}
        },
        "ai_memory": {}
    }


@pytest.fixture
def sample_world_state():
    """기본 월드 상태 픽스처"""
    return {
        "time_slot": "오후",
        "weather": "맑음",
        "day": 1,
        "doom": 0,
        "risk_level": "None",
        "current_location": "마을 광장",
        "location_rules": {},
    }
