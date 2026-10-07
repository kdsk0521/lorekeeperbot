"""
Lorekeeper TRPG Bot - Prompt Builder Utilities (유틸리티 라이브러리)

[V3 아키텍처 업데이트]
이 모듈은 이제 유틸리티 함수 라이브러리로 동작합니다.
실제 34단계 프롬프트 오케스트레이션은 `slot_manager.py`에서 담당합니다.

═══════════════════════════════════════════════════════════════════
📦 유틸리티 함수 (slot_manager.py에서 재사용):
  - build_length_instruction()    : 응답 길이 지시문
  - build_combined_directive()    : 장르/톤 기반 지시문 빌더
  - build_mature_content_prompt() : 성숙 콘텐츠 가이드라인 (Slot 22)

📦 상수:
  - _SCENE_TYPE_DESCRIPTION : 씬 타입 한 줄 서술 (인가 선언문 삽입용)

⚠️ 2026-08-01 정리: 아래 6심볼은 소비처 0으로 제거됨 —
  get_scene_type_description / get_available_genres / get_genre_description /
  build_length_instruction / SCENE_TYPES / GENRE_DEFINITIONS.
  (PromptBuilder 클래스는 그 이전에 이미 제거, slot_manager.SlotPromptBuilder가 대체)
═══════════════════════════════════════════════════════════════════
"""

import logging
from typing import Optional, List, Dict, Any, Union

logger = logging.getLogger(__name__)


# =========================================================
# 응답 길이 설정
# =========================================================
# =========================================================
# [2026-08-01 죽은 코드 정리] 아래 5심볼 제거 — 소비처 0 실측(전체 grep, tests 제외):
#   GENRE_DEFINITIONS / SCENE_TYPES / get_available_genres / get_genre_description /
#   get_scene_type_description / build_length_instruction (+ DEFAULT_MIN/MAX_RESPONSE_LENGTH)
# 근거: slot_manager(유일 소비자)가 쓰는 것은 build_combined_directive·build_mature_content_prompt
#   둘뿐. GENRE_DEFINITIONS는 genre_hints와 같은 14장르를 다르게 서술한 중복이었고 미사용 쪽이다.
#   길이 계약은 persona가 담당(min_length = max(1000, max_chars * _FLOOR_BY_ENERGY) + 문단 수 파생)
#   — 여기 DEFAULT 1500~2500은 구 시스템 잔재로 서로 어긋나 있었다.
# 롤백: git history. 장르 목록 UI가 필요해지면 genre_hints.keys()로 충분하다.
#
# ⚠[2026-08-01 사후 수리] 위 정리에서 `get_scene_type_description`이 **아직 쓰이는 채로**
#   삭제됐다 — build_mature_content_prompt(L147) 안에서 자기 파일이 호출하고 있었다.
#   "소비처 0 실측(전체 grep)"이 **정의 파일 바깥만** 셌기 때문. 결과는 린트 경고가 아니라
#   **라이브 NameError**: content_level != 'normal'(gore/nsfw/gore_nsfw) 인 모든 턴에서
#   Slot 22 인가 블록 조립이 터진다. 인가는 (d)존(load-bearing)이라 조용한 실패도 위험하다.
#   → 함수를 되살리지 않고 아래 표로 인라인. 미지 scene_type도 기본 문구로 착지(재크래시 0).
#   교훈: 자기 파일 내부 호출은 "외부 소비처 0"에 안 잡힌다(dead_scan v2가 v1에서 배운 것과 같은 병).
# =========================================================

# 씬 타입 한 줄 서술 — Slot 22 인가 선언문에 삽입.
# 문면은 K2 언어(문장체), 기능은 불변: "이 장면은 X를 온전히 그린다"는 선언.
_SCENE_TYPE_DESCRIPTION = {
    'gore': "violence and physical harm render in full wherever a scene reaches them.",
    'nsfw': "explicit intimacy renders in full wherever a scene reaches it.",
    'gore_nsfw': "violence and explicit intimacy both render in full wherever a scene reaches them.",
}
_SCENE_TYPE_DESCRIPTION_DEFAULT = "mature material renders in full wherever a scene reaches it."

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
        'high_fantasy': "- Deeds outlive the doer; the world keeps score in legend. Whether the legend flatters or indicts, it is the record that survives.",
        'wuxia': "- Debt of honor (義俠) and grievance (恩怨) outrank law; a technique settles what words cannot. Mastery is earned in isolation and spent in public.",
        'cyberpunk': "- Capability is purchasable; dignity is not. Where the tech is new the gap is visible; where it is old the gap has become the floor.",
        'post_apocalypse': "- Every use consumes; nothing is replaced, and absence does the describing. Early on scarcity is the adversary; later it is whoever holds what is left.",
        'space_opera': "- Distance makes strangers of neighbours; scale turns decisions into policy. Contact is expensive, so whatever crosses the gap arrives distorted.",
        'modern': "- Competence is the currency, and institutions absorb what individuals intend. Little is forbidden and little is simple; the friction is procedural.",

        # [B. The Flavor]
        'urban_fantasy': "- The impossible obeys local rules and pays local costs. Hidden or acknowledged, it has to fit inside a city that keeps working.",
        'steampunk': "- Machinery is visible and fallible; progress announces itself in noise and soot. Invention outruns the law that would govern it.",
        'cosmic_horror': "- Understanding costs more than ignorance did, and scale never resolves. Contact leaves a mark that outlasts the encounter.",
        'game_system': "- Rules are visible to those inside; progress is measured and therefore gamed. What the numbers cannot count still decides outcomes.",
        'occult': "- The procedure works and the price is exact; knowing the rule does not make it cheap. What is invoked keeps its own terms.",

        # [C. The Lens (Tone Quartet)]
        'noir': "- Every answer is bought; the price compounds.",
        'comedy': "- Intent and outcome stay out of register.",
        'romance': "- Every change in distance lands as event.",
        'drama': "- Consequence outpaces resolution."
    }

    directives = []

    # [1] Genre Directives
    if active_genres:
        for genre in active_genres:
            if genre.lower() in genre_hints:
                directives.append(genre_hints[genre.lower()])

    # [2] Custom Tone Directive ([2026-07-22 잔여 정리] [GUIDE] 태그 → 문장, K2)
    if custom_tone:
        directives.append(f"- The table's own note for this run: {custom_tone}")

    # [2026-09-29 배치1 C1·C2] 구 [3] Universal Writing Guidelines·Global_Directives 삭제.
    #   THIS TURN 존(최대 recency)에 매 턴 실려 말 못 하는 장면에도 대사 균형·오감 메뉴를 걸었다.
    #   PROSE_CRAFT(리듬·감각·대사)·NPC_BEHAVIOR·Output purity가 이미 쥔다(조립부_3분할지도 §0-4).

    # Construct Block
    narrative_section = "\n".join(directives) if directives else "- Maintain the current atmosphere and tone."

    return f'''<Lorekeeper_Umbrella type="narrative_directive" layers="1">
<Stage_Directives>
{narrative_section}
</Stage_Directives>
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
    # [2026-07-22 Phase 3-b] 상태-필드 선언(Authorization Status:/Restriction Level:) → 문장.
    # ⚠기능은 (d)존 불변 — 인가 자체는 그대로, 기계 문면만 K2 언어로.
    # [2026-10-01 1차] 인가 줄("Mature content is authorized here and the usual restriction does not apply …") 삭제 —
    #   순수 탈옥 끄기(렌더만, 레티어스). 장면 유형 선언 줄은 남긴다. 호출처 = slot_manager 렌더 조립 1곳.
    #   스펙 composition/분석렌더_1차_구현스펙_2026-10-01.md §4-6.
    _desc = _SCENE_TYPE_DESCRIPTION.get(scene_type, _SCENE_TYPE_DESCRIPTION_DEFAULT)
    prompt_parts.append(f"""
<Content_Range>
Range at this table ({scene_type.upper()}): {_desc}
</Content_Range>
""")

    # Add relevant guidelines
    if 'gore' in scene_type:
        prompt_parts.append(text_resources.VISCERAL_CONTENT_GUIDELINES)

    if 'nsfw' in scene_type:
        prompt_parts.append(text_resources.MATURE_CONTENT_GUIDELINES)
    # Add hybrid protocol when both gore and nsfw are present
    if 'gore' in scene_type and 'nsfw' in scene_type:
        prompt_parts.append(text_resources.HYBRID_CONTENT_PROTOCOL)

    # [2026-08-02] 인격 보존(받는 쪽·가하는 쪽) — 수위 씬 **공통**, 한 번만.
    #   구 위치는 HYBRID(POWER EXCHANGE INTEGRITY + EMOTIONAL COMPLEXITY)였는데 gore_nsfw에서만
    #   발화하므로 정작 "이성 상실·본능만 남음"이 잘 나오는 gore 단독·nsfw 단독에서
    #   규칙이 빠져 있었다. VISCERAL/MATURE에 각각 복사하면 gore_nsfw에서 이중 투입 →
    #   여기서 1회 주입한다. 이 함수는 scene_type != 'normal' 일 때만 도달한다.
    _idu = getattr(text_resources, 'PERSONHOOD_AT_INTENSITY', '')
    if _idu:
        prompt_parts.append(_idu)

    return "\n".join(prompt_parts)





# =========================================================
# [V3 UPDATE] PromptBuilder 클래스 제거됨
# =========================================================
# 모든 프롬프트 조립은 slot_manager.SlotPromptBuilder가 담당한다.
# 이 모듈에 남은 것은 slot_manager가 실제로 부르는 둘뿐:
# - build_combined_directive()     → Slot 33 저자노트
# - build_mature_content_prompt()  → Slot 22 인가
# (구 목록에 있던 나머지는 2026-08-01 제거 — 파일 상단 주석 참조)

# [END OF FILE]