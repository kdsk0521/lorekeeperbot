"""
Lorekeeper TRPG Bot - Prompt Builder Module
AI 프롬프트 조립을 담당하는 모듈입니다.

역할:
- PromptBuilder 클래스: SillyTavern 프리셋 순서에 맞게 프롬프트 조립
- 장르/톤 기반 지시문 생성
- 성숙 콘텐츠 가이드라인 조립

persona.py의 프롬프트 상수들을 사용하여 최종 프롬프트를 조립합니다.
상수 자체는 persona.py에 유지되어 서사의 핵심이 손상되지 않습니다.
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
    import persona

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
        prompt_parts.append(persona.GORE_CONTENT_GUIDELINES)

    if 'nsfw' in scene_type:
        prompt_parts.append(persona.NSFW_CONTENT_GUIDELINES)

    return "\n".join(prompt_parts)


# =========================================================
# PromptBuilder Class (프롬프트 빌더 클래스)
# =========================================================
class PromptBuilder:
    """
    SillyTavern 프리셋 순서에 맞게 프롬프트를 조립합니다.

    순서:
    1. AI Mandate & Core Constraints
    2. The Axiom Of The World
    3. <Lore> 로어북 </Lore>
    4. <Roles> 페르소나 프롬프트, 캐릭터 설명 </Roles>
    5. <Fermented> 에피소드 요약, 장기 기억 </Fermented>
    6. <Immediate> 과거 챗 </Immediate>
    7. =====CACHE BOUNDARY=====
    8. <Scripts> 작노, 글노, 최종 삽입 프롬프트 </Scripts>
    9. # Core Models
    10. <Current-Context> 최근 챗 </Current-Context>
    11. <유저 메시지> / OOC
    12. Output Generation Request
    13. 언어 출력 교정
    """

    def __init__(self):
        self.sections = {}

    def set_lore(self, lore_text: str, rule_text: str = "") -> 'PromptBuilder':
        """[3] 로어북 설정"""
        self.sections['lore'] = f"""
<Lore>
### 세계관 (World Setting)
{lore_text}

### 규칙 (Rules)
{rule_text if rule_text else "(Standard TRPG rules apply)"}
</Lore>
"""
        return self

    def set_player_info(self, name: str, desc: str = "") -> 'PromptBuilder':
        """[3.5] 플레이어 캐릭터 정보 설정 (PC 사칭 방지 핵심)"""
        if not name or name == "Unknown":
            return self

        self.sections['player_info'] = f"""
<Player_Character>
Name: {name}
Description: {desc}
⚠️ CRITICAL: This character is the PLAYER. Controlled ONLY by User input.
🛑 ABSOLUTELY FORBIDDEN: Do NOT write dialogue or actions for {name}.
</Player_Character>
"""
        return self

    def set_roles(
        self,
        character_descriptions: str = "",
        persona_prompt: str = ""
    ) -> 'PromptBuilder':
        """[4] 캐릭터 설명 및 페르소나 프롬프트"""
        import persona
        # [V6 Refactor] Ensure persona prompt defaults to RECORDER_IDENTITY if empty
        actual_persona = persona_prompt if persona_prompt.strip() else persona.RECORDER_IDENTITY

        self.sections['roles'] = f"""
<Roles>
### 페르소나 프롬프트
{actual_persona}

### 캐릭터 설명
{character_descriptions if character_descriptions.strip() else "(Characters defined in Lore)"}
</Roles>
"""
        return self

    def set_fermented(
        self,
        episode_summary: str = "",
        deep_memory: str = ""
    ) -> 'PromptBuilder':
        """[5] 발효된 기억 (에피소드 요약, 장기 기억)"""
        content = ""
        if deep_memory:
            content += f"### Deep Memory (초장기 기억)\n{deep_memory}\n\n"
        if episode_summary:
            content += f"### Episode Summary (에피소드 요약)\n{episode_summary}\n"

        if content:
            self.sections['fermented'] = f"""
<Fermented>
{content}
</Fermented>
"""
        else:
            self.sections['fermented'] = ""
        return self

    def set_immediate(self, past_chat: str = "") -> 'PromptBuilder':
        """[6] 즉시 기억 (과거 챗)"""
        if past_chat:
            self.sections['immediate'] = f"""
<Immediate>
### 과거 대화 기록
{past_chat}
</Immediate>
"""
        else:
            self.sections['immediate'] = ""
        return self

    def set_scripts(
        self,
        author_note: str = "",
        writing_note: str = "",
        final_insert: str = "",
        active_genres: Optional[List[str]] = None,
        custom_tone: Optional[str] = None
    ) -> 'PromptBuilder':
        """[8] 스크립트 (작노, 글노, 최종 삽입) - 장르/톤 기반 동적 생성"""
        # 커스텀 노트가 제공되면 그것을 사용, 아니면 장르/톤 기반 생성
        if author_note or writing_note:
            scripts = ""
            if author_note:
                scripts += f"### 작가 노트\n{author_note}\n\n"
            if writing_note:
                scripts += f"### 글쓰기 노트\n{writing_note}\n\n"
            if final_insert:
                scripts += f"### 최종 삽입\n{final_insert}\n"

            self.sections['scripts'] = f"""
<Scripts>
{scripts}
</Scripts>
"""
        else:
            # 장르/톤 기반 동적 생성
            genres = active_genres or self.sections.get('_active_genres', None)
            tone = custom_tone or self.sections.get('_custom_tone', None)

            # Use build_combined_directive for dynamic generation
            self.sections['scripts'] = build_combined_directive(genres, tone)

            if final_insert:
                self.sections['scripts'] += f"\n<Scripts type='final_insert'>\n{final_insert}\n</Scripts>"

        return self

    def set_current_context(
        self,
        recent_chat: str = "",
        world_state: str = "",
        nvc_analysis: str = ""
    ) -> 'PromptBuilder':
        """[10] 현재 컨텍스트 (최근 챗)"""
        content = ""
        if world_state:
            content += f"### World State\n{world_state}\n\n"
        if nvc_analysis:
            content += f"### Left Hemisphere Analysis\n{nvc_analysis}\n\n"
        if recent_chat:
            content += f"### Recent Chat\n{recent_chat}\n"

        if content:
            self.sections['current_context'] = f"""
<Current-Context>
{content}
</Current-Context>
"""
        else:
            self.sections['current_context'] = ""
        return self

    def set_user_message(
        self,
        material: str,
        ooc_content: str = ""
    ) -> 'PromptBuilder':
        """[11] 유저 메시지"""
        ooc_section = ""
        if ooc_content:
            ooc_section = f"\n### OOC 지시\n{ooc_content}\n"

        self.sections['user_message'] = f"""
<User_Message>
### Material (플레이어 입력)
<material>
{material}
</material>
{ooc_section}
</User_Message>
"""
        return self

    def set_genres(self, active_genres: Union[List[str], Dict[str, Any], None] = None) -> 'PromptBuilder':
        """활성 장르 설정 (Supports List or 3-Layer Dict)"""
        import re

        # Normalize to list for internal hint lookup, but keep structure for display
        display_text = ""
        normalized_list = []

        # Helper to normalize layer values (List or String)
        def _normalize_layer(val) -> List[str]:
            if isinstance(val, list):
                return [str(v).strip().lower() for v in val if v]
            if isinstance(val, str):
                # Legacy mix string support
                raw = re.split(r'[+/&,]', val)
                return [r.strip().lower() for r in raw if r.strip()]
            return []

        if isinstance(active_genres, dict) and "layers" in active_genres:
            # New 3-Layer Format
            layers = active_genres["layers"]
            display_text = "### ACTIVE GENRE ARCHETYPES (3-Layer Analysis)\n"

            # Layer 1: World
            w_list = _normalize_layer(layers.get("world_setting", "modern"))
            w_display = ", ".join([x.upper() for x in w_list])
            display_text += f"- **[STAGE] World Setting:** {w_display} \n"
            normalized_list.extend(w_list)

            # Layer 2: Style (Optional)
            s_list = _normalize_layer(layers.get("style_tech", []))
            if s_list:
                s_display = ", ".join([x.upper() for x in s_list])
                display_text += f"- **[SKIN] Style & Tech:** {s_display} \n"
                normalized_list.extend(s_list)

            # Layer 3: Tone (Optional)
            t_list = _normalize_layer(layers.get("narrative_tone", []))
            if t_list:
                t_display = ", ".join([x.upper() for x in t_list])
                display_text += f"- **[LENS] Narrative Tone:** {t_display} \n"
                normalized_list.extend(t_list)

            if "atmosphere_guide" in active_genres:
                display_text += f"\n**[GUIDE]:** {active_genres['atmosphere_guide']}\n"

            self.sections['_active_genres'] = normalized_list

        elif isinstance(active_genres, list):
            # Legacy List Format
            normalized_list = active_genres
            self.sections['_active_genres'] = normalized_list

            display_text = "### ACTIVE GENRE MODULES\n"
            for genre in active_genres:
                display_text += f"- **{genre.upper()}**: (Active)\n"

        else:
            self.sections['_active_genres'] = []
            return self

        self.sections['genres'] = display_text + "\n"
        return self

    def set_custom_tone(self, custom_tone: Optional[str] = None) -> 'PromptBuilder':
        """커스텀 톤 설정"""
        self.sections['_custom_tone'] = custom_tone  # 내부 저장용
        if custom_tone:
            self.sections['custom_tone'] = f"""
### ATMOSPHERE OVERRIDE
**Directive:** Filter all descriptions through this atmospheric lens:
> {custom_tone}
This tone affects style, not physics or causality.
"""
        return self

    def set_scene_type(self, scene_type: Optional[str] = None) -> 'PromptBuilder':
        """
        장면 유형 설정 (고어/NSFW 묘사 활성화용)

        Args:
            scene_type: 'normal', 'gore', 'nsfw', 'gore_nsfw' 중 하나

        Returns:
            self (체이닝용)
        """
        self.sections['_scene_type'] = scene_type  # 내부 저장용
        if scene_type and scene_type != 'normal':
            mature_prompt = build_mature_content_prompt(scene_type)
            if mature_prompt:
                self.sections['mature_content'] = mature_prompt
        return self

    def set_cognition_data(self, nvc_summary: str = "", psych_profile: Optional[Dict[str, Any]] = None) -> 'PromptBuilder':
        """인지 엔진(Left Brain)의 분석 데이터 및 심리 프로필 설정"""
        content = nvc_summary
        
        if psych_profile:
            needs = psych_profile.get("needs", {})
            values = psych_profile.get("values", [])
            instinct = psych_profile.get("instinct", "neutral")
            
            # Format Psych Profile
            psych_text = f"\n### PSYCHOLOGICAL PROFILE (Real-time State)\n"
            psych_text += f"- **Dominant Instinct**: {instinct.upper()}\n"
            psych_text += f"- **Core Values**: {', '.join(values) if values else 'None'}\n"
            psych_text += "- **Need State**:\n"
            for k, v in needs.items():
                # Simple visualizer for needs (-50 to +50)
                state_desc = "Satisfied" if v > 20 else "Deprived (CRITICAL)" if v < -20 else "Stable"
                psych_text += f"  - {k.capitalize()}: {v} ({state_desc})\n"
            
            content += psych_text

        self.sections['nvc_summary'] = content
        return self

    def build_system_prompt(self) -> str:
        """
        Build system prompt (order 1-7)
        Static content before cache boundary
        """
        # Import constants from persona (lazy import)
        import persona

        parts = [
            # [0] Content Authorization Mandate
            persona.CONTENT_AUTHORIZATION_MANDATE,

            # [1] AI Mandate & Core Constraints
            persona.AI_MANDATE,
            persona.MEMORY_HIERARCHY,

            # [2] The Axiom Of The World
            persona.WORLD_AXIOM,
            persona.WORLD_AXIOM,
            persona.PERCEPTION_CONSTRAINTS,
            persona.ANTI_DIDACTIC_PRINCIPLES,
            persona.TELESCOPE_PROTOCOL,
            persona.AI_MORAL_BIAS_PROHIBITION,

            # [2.5] PC Autonomy Doctrine
            persona.PC_AUTONOMY_DOCTRINE,

            # Core Instruction Components
            persona.INTERACTION_MODEL,
            persona.SOCIAL_DYNAMICS,
            persona.TEMPORAL_DYNAMICS,
            persona.RECORDER_IDENTITY,
            persona.ACTION_RESOLUTION,
            persona.CHARACTER_CONSISTENCY_PROTOCOL,
            persona.NPC_ATTITUDE_ENFORCEMENT,
            persona.TIME_ATMOSPHERE,
            persona.ASPECT_UTILIZATION,
            persona.WRITING_STYLE_ENFORCEMENT,
            persona.SELF_CORRECTION_PROTOCOL,
            persona.CRITICAL_PRIORITY,
            persona.MATERIAL_PROCESSING_PROTOCOL,
        ]

        # Add dynamic static content
        if 'genres' in self.sections:
            parts.append(self.sections['genres'])
        if 'custom_tone' in self.sections:
            parts.append(self.sections['custom_tone'])
        if 'mature_content' in self.sections:
            parts.append(self.sections['mature_content'])
        if 'lore' in self.sections:
            parts.append(self.sections['lore'])
        if 'player_info' in self.sections:
            parts.append(self.sections['player_info'])
        if 'roles' in self.sections:
            parts.append(self.sections['roles'])
        if 'fermented' in self.sections:
            parts.append(self.sections['fermented'])
        if 'immediate' in self.sections:
            parts.append(self.sections['immediate'])

        # Final XML encapsulation
        system_block = f"""
<System_Instruction_Set version="v2.5_Antigravity">
{''.join([f"<{i}>{p}</{i}>" for i, p in enumerate(parts)])}
</System_Instruction_Set>
"""
        return system_block

    def build_dynamic_prompt(self) -> str:
        """
        동적 프롬프트 빌드 (8-13번 순서)
        캐시 경계 이후의 동적 컨텐츠
        """
        import persona

        # Scripts가 설정되지 않았으면 장르/톤 기반으로 자동 생성
        if 'scripts' not in self.sections:
            active_genres = self.sections.get('_active_genres')
            custom_tone = self.sections.get('_custom_tone')
            self.sections['scripts'] = build_combined_directive(active_genres, custom_tone)

        dynamic_parts = [
            # [8] Scripts
            self.sections.get('scripts', ''),

            # [10-11] Raw Context & User Message
            f"<Current_Context>{self.sections.get('current_context', '')}</Current_Context>",
            f"<Material>{self.sections.get('user_message', '')}</Material>",

            # [Cognition Data Sync]
            f"<Cognition_Engine_Data>{self.sections.get('nvc_summary', '')}</Cognition_Engine_Data>",

            # [12-14] Requests & Enforcements
            persona.OUTPUT_GENERATION_REQUEST,
            persona.LANGUAGE_CORRECTION,
            persona.FINAL_AUTONOMY_ENFORCEMENT,
            build_length_instruction(),
            persona.EMOTION_BOOSTER,
        ]

        return "\n==========CACHE BOUNDARY==========\n" + "\n\n".join(filter(None, dynamic_parts))

    def build_full_prompt(self) -> str:
        """전체 프롬프트 빌드"""
        return self.build_system_prompt() + "\n\n" + self.build_dynamic_prompt()


# =========================================================
# 시스템 프롬프트 구성 (기존 호환성 유지)
# =========================================================
def construct_system_prompt(
    active_genres: Optional[List[str]] = None,
    custom_tone: Optional[str] = None
) -> str:
    """
    장르와 톤을 기반으로 시스템 프롬프트를 조립합니다.
    (기존 API 호환성 유지)
    """
    builder = PromptBuilder()
    builder.set_genres(active_genres)
    builder.set_custom_tone(custom_tone)
    return builder.build_system_prompt()
