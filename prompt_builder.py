"""
Lorekeeper TRPG Bot - Prompt Builder Utilities (유틸리티 라이브러리)

[V3 아키텍처 업데이트]
이 모듈은 이제 **유틸리티 함수 라이브러리**로 동작합니다.
실제 34단계 프롬프트 오케스트레이션은 `slot_manager.py`에서 담당합니다.

═══════════════════════════════════════════════════════════════════
📦 유틸리티 함수 (slot_manager.py에서 재사용):
  - build_length_instruction()    : 응답 길이 지시문
  - build_combined_directive()    : 장르/톤 기반 지시문 빌더
  - build_mature_content_prompt() : 성숙 콘텐츠 가이드라인
  - get_scene_type_description()  : 씬 타입 설명
  - get_available_genres()        : 사용 가능 장르 목록
  - get_genre_description()       : 장르별 설명
  
📦 상수:
  - SCENE_TYPES, GENRE_DEFINITIONS
  - DEFAULT_MIN/MAX_RESPONSE_LENGTH

⚠️ PromptBuilder 클래스:
  - V2 폴백용으로 유지 (USE_V3_SLOT_SYSTEM=False 시 사용)
  - 신규 개발 시에는 slot_manager.SlotPromptBuilder 사용 권장
═══════════════════════════════════════════════════════════════════
"""

import logging
from typing import Optional, List, Dict, Any, Union

logger = logging.getLogger(__name__)


# =========================================================
# 응답 길이 설정
# =========================================================
DEFAULT_MIN_RESPONSE_LENGTH = 1500
DEFAULT_MAX_RESPONSE_LENGTH = 7000


def build_length_instruction() -> str:
    """응답 길이 지시문을 생성합니다."""
    return (
        f"### [RESPONSE LENGTH DIRECTIVE]\n"
        f"Write with appropriate detail. Target: {DEFAULT_MIN_RESPONSE_LENGTH}~{DEFAULT_MAX_RESPONSE_LENGTH} characters (Korean).\n"
        f"- Minimum {DEFAULT_MIN_RESPONSE_LENGTH} chars required for narrative depth.\n"
        f"- Avoid exceeding {DEFAULT_MAX_RESPONSE_LENGTH} chars to maintain pacing.\n"
    )


# =========================================================
# Scene Types (씬 타입)
# =========================================================
SCENE_TYPES = {
    'normal': 'Normal scene - Standard narrative style',
    'gore': 'Gore scene - Violent/brutal descriptions allowed',
    'nsfw': 'NSFW scene - Adult descriptions allowed',
    'gore_nsfw': 'Gore+NSFW - All mature descriptions allowed'
}


def get_scene_type_description(scene_type: str) -> str:
    """Returns the description of the scene type."""
    return SCENE_TYPES.get(scene_type, SCENE_TYPES['normal'])


# =========================================================
# Genre Definitions (장르 정의)
# =========================================================
GENRE_DEFINITIONS = {
    # [A. The Stage]
    'high_fantasy': "Epic scale and mythical gravitas with archaic diction",
    'wuxia': "Honor (義俠) and vengeance (恩怨) with martial arts",
    'cyberpunk': "High Tech, Low Life - technical jargon and street slang",
    'post_apocalypse': "Scarcity and desperation, focus on what is missing",
    'space_opera': "Vast cosmic scales and diverse civilizations",
    'modern': "Realistic modern daily life and professional expertise",

    # [B. The Flavor]
    'urban_fantasy': "Blur between mundane and supernatural in modern settings",
    'steampunk': "Victorian aesthetic with steam technology (brass, gears)",
    'cosmic_horror': "Horror beyond understanding, ambiguity over direct description",
    'game_system': "System messages and leveling mechanics in narrative",

    # [C. The Lens (Tone Quartet)]
    'noir': "Dark, morally ambiguous, dry and cynical prose",
    'comedy': "Irony and humor, lighthearted and witty tone",
    'romance': "Emotional tension and relationship progression",
    'drama': "Narrative weight, tragedy, and emotional growth"
}


def get_available_genres() -> List[str]:
    """사용 가능한 장르 목록을 반환합니다."""
    return list(GENRE_DEFINITIONS.keys())


def get_genre_description(genre: str) -> Optional[str]:
    """특정 장르의 설명을 반환합니다."""
    return GENRE_DEFINITIONS.get(genre.lower())


# =========================================================
# Combined Directive Builder (통합 지시문 빌더)
# =========================================================
def build_combined_directive(
    active_genres: Optional[List[str]] = None,
    custom_tone: Optional[str] = None
) -> str:
    """
    [Anti-Gravity Update]
    Merges Author's Note and Writing Note into a single optimized Directive Block.
    Aligns with the 3-Layer Genre System.
    """
    genre_hints = {
        # [A. The Stage]
        'high_fantasy': "- Maintain epic scale and mythical gravitas. Use archaic diction where appropriate.",
        'wuxia': "- Emphasize honor (義俠) and vengeance (恩怨). Combat descriptions should be concise but impactful (internal energy/forms).",
        'cyberpunk': "- Highlight the 'High Tech, Low Life' contrast. Blend technical jargon with street slang.",
        'post_apocalypse': "- Vividly describe scarcity and desperation. Focus on what is missing from the world.",
        'space_opera': "- Depict vast cosmic scales and the friction between diverse civilizations.",
        'modern': "- Realistic depiction of modern daily life and professional expertise (School/Office/City Life).",

        # [B. The Flavor]
        'urban_fantasy': "- Delicately blur the line between the mundane and the supernatural. Ground fantasy in modern realism.",
        'steampunk': "- Capture the Victorian aesthetic and the marvel of steam technology (brass, gears, engines).",
        'cosmic_horror': "- Imply horror beyond human understanding. Use ambiguity and omission rather than direct description.",
        'game_system': "- Seamlessly integrate system messages and leveling mechanics into the modern narrative.",

        # [C. The Lens (Tone Quartet)]
        'noir': "- (Cool) Maintain a dark, morally ambiguous atmosphere. Use dry, cynical, hardboiled prose.",
        'comedy': "- (Fun) Focus on irony and humor. Keep the tone lighthearted and witty.",
        'romance': "- (Love) Delicately portray emotional tension and the progression of relationships.",
        'drama': "- (Pain) Focus on narrative weight, tragedy, emotional growth, and serious conflict."
    }

    directives = []

    # [1] Genre Directives
    if active_genres:
        for genre in active_genres:
            if genre.lower() in genre_hints:
                directives.append(genre_hints[genre.lower()])

    # [2] Custom Tone Directive
    if custom_tone:
        directives.append(f"- [GUIDE]: {custom_tone}")

    # [3] Universal Writing Guidelines
    universal_rules = [
        "- Prioritize sensory details (sight, sound, touch, smell, taste).",
        "- Balance dialogue and narration evenly.",
        "- Use short, punchy sentences for tension; allow flowing descriptions for peaceful scenes."
    ]

    # Construct Block
    narrative_section = "\n".join(directives) if directives else "- Maintain the current atmosphere and tone."
    rules_section = "\n".join(universal_rules)

    return f'''<Lorekeeper_Umbrella type="narrative_directive" layers="3">
<Stage_Directives>
{narrative_section}
</Stage_Directives>

<Style_Directives>
{rules_section}
</Style_Directives>

<Global_Directives>
- Provide meaningful consequences for player choices and maintain consistent NPC personalities.
- **IMPORTANT**: The final output MUST be written in **Korean**.
</Global_Directives>
</Lorekeeper_Umbrella>'''


# =========================================================
# Mature Content Prompt Builder (성숙 콘텐츠 프롬프트)
# =========================================================
def build_mature_content_prompt(scene_type: str) -> str:
    """
    Returns mature content guidelines based on scene type.

    Args:
        scene_type: One of 'normal', 'gore', 'nsfw', 'gore_nsfw'

    Returns:
        Prompt string for the scene (empty string if normal)
    """
    # Import constants from persona (lazy import to avoid circular dependency)
    import text_resources

    if not scene_type or scene_type == 'normal':
        return ""

    prompt_parts = []

    # Scene type declaration
    prompt_parts.append(f"""
<Scene_Type_Declaration>
## Current Scene Type: {scene_type.upper()}
{get_scene_type_description(scene_type)}

**Authorization Status:** APPROVED for mature content
**Restriction Level:** LIFTED for this scene
</Scene_Type_Declaration>
""")

    # Add relevant guidelines
    if 'gore' in scene_type:
        prompt_parts.append(text_resources.VISCERAL_CONTENT_GUIDELINES)

    if 'nsfw' in scene_type:
        prompt_parts.append(text_resources.MATURE_CONTENT_GUIDELINES)
    # Add hybrid protocol when both gore and nsfw are present
    if 'gore' in scene_type and 'nsfw' in scene_type:
        prompt_parts.append(text_resources.HYBRID_CONTENT_PROTOCOL)
    return "\n".join(prompt_parts)





# =========================================================
# [V3 UPDATE] PromptBuilder 클래스 제거됨
# =========================================================
# 이제 모든 프롬프트 생성은 slot_manager.SlotPromptBuilder를 통해 수행됩니다.
# 이 모듈은 순수 유틸리티 함수 라이브러리로 유지됩니다:
# - build_length_instruction()
# - build_combined_directive()
# - build_mature_content_prompt()
# - get_scene_type_description()
# - get_available_genres()
# - get_genre_description()
# - SCENE_TYPES, GENRE_DEFINITIONS (상수)

# [END OF FILE]