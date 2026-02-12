"""
Lorekeeper Bot — Scenario Simulation Test (v2.1)
Theoria 분석 + 서사 파이프라인만 테스트. 둠/멘탈/판정/이변 제외.

사용법:
  python tests/test_simulation.py                    # 전체 시나리오 실행
  python tests/test_simulation.py --scenario 침묵     # 특정 시나리오만
  python tests/test_simulation.py --list              # 시나리오 목록
  python tests/test_simulation.py --prompt            # 프롬프트 빌드 결과 출력
  python tests/test_simulation.py --prompt --slot 14  # 특정 슬롯만 출력

시나리오 추가:
  SCENARIOS 딕셔너리에 새 항목 추가하면 자동 실행됨.
"""

import sys
import os
import io
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# =========================================================
# Mock Domain Manager (API 없이 테스트)
# =========================================================
class MockDomainManager:
    def __init__(self):
        self._domains = {}

    def _ensure(self, cid):
        if cid not in self._domains:
            self._domains[cid] = {
                "participants": {},
                "world_state": {"doom": 0, "time_slot": "오전", "day": 1},
                "npcs": {},
                "history": [],
                "lore": "",
                "lore_chunks": [],
                "settings": {"impersonation_filter": True},
                "npc_attitudes": {},
                "fermented_summary": "",
                "bot_active": True,
            }
        return self._domains[cid]

    def get_domain(self, cid):
        return self._ensure(cid)

    def get_participant_data(self, cid, uid):
        d = self._ensure(cid)
        return d["participants"].get(uid, {})

    def save_participant_data(self, cid, uid, data):
        d = self._ensure(cid)
        d["participants"][uid] = data

    def update_participant(self, cid, user, mask):
        d = self._ensure(cid)
        uid = str(user.id) if hasattr(user, 'id') else str(user)
        d["participants"][uid] = {
            "mask": mask, "status": "active",
            "passives": [], "status_effects": [], "ai_memory": {},
        }

    def get_world_state(self, cid):
        return self._ensure(cid).get("world_state", {})

    def update_world_state(self, cid, val):
        self._ensure(cid)["world_state"] = val

    def get_npcs(self, cid):
        return self._ensure(cid).get("npcs", {})

    def append_history(self, cid, role, content):
        self._ensure(cid)["history"].append({"role": role, "content": content})

    def get_history(self, cid):
        return self._ensure(cid).get("history", [])

    def get_bot_active(self, cid):
        return self._ensure(cid).get("bot_active", True)

    def set_bot_active(self, cid, val):
        self._ensure(cid)["bot_active"] = val

    def get_npc_attitudes(self, cid):
        return self._ensure(cid).get("npc_attitudes", {})

    def set_npc_attitudes(self, cid, val):
        self._ensure(cid)["npc_attitudes"] = val

    def get_unified_player_info(self, cid, uid):
        p = self.get_participant_data(cid, uid)
        if not p:
            return ""
        lines = [f"Name: {p.get('mask', 'Unknown')}"]
        if p.get("passives"):
            lines.append(f"특질: {', '.join(str(x) for x in p['passives'])}")
        return "\n".join(lines)

    def add_to_ai_memory_list(self, cid, uid, key, item):
        p = self.get_participant_data(cid, uid)
        mem = p.setdefault("ai_memory", {})
        lst = mem.setdefault(key, [])
        lst.append(item)

    def get_lore(self, cid):
        return self._ensure(cid).get("lore", "")

    def get_fermented_summary(self, cid):
        return self._ensure(cid).get("fermented_summary", "")


# Monkey-patch domain_manager
import domain_manager
mock_dm = MockDomainManager()
for attr in dir(mock_dm):
    if not attr.startswith('_') and callable(getattr(mock_dm, attr)):
        setattr(domain_manager, attr, getattr(mock_dm, attr))

import npc_manager
import slot_manager
import text_resources

# =========================================================
# 시나리오 정의 — 분석/서사 중심 (v2.1 psyche 스키마 사용)
# =========================================================

CHANNEL = "SIM_TEST"
USER_ID = "SIM_USER"

SCENARIOS = {
    # ----------------------------------------------------------
    "침묵": {
        "desc": "아무 말도 하지 않는다. 서사 엔진이 침묵을 어떻게 처리하는지.",
        "world": {"doom": 25, "time_slot": "밤", "day": 3},
        "pc": {
            "mask": "유진",
            "passives": [],
            "status_effects": [],
            "ai_memory": {"vigor": {"value": 65}, "composure": {"value": 70}},
        },
        "npcs": {
            "소연": {"description": "유진의 오랜 친구. 최근 뭔가 숨기는 게 있다. 시선을 자주 피한다.", "source": "lore"},
        },
        "npc_attitudes": {
            "소연": {"attitude": "friendly", "trajectory": "declining", "depth": 40, "tension": 25},
        },
        "user_input": "아무 말 없이 소연을 바라본다",
        "dai": {
            "input_analysis": {"Original": "아무 말 없이 소연을 바라본다", "Enhanced": "유진이 침묵 속에서 소연의 눈을 찾는다", "Plausibility": "High", "Momentum": "Open"},
            "observation": "유진이 아무 말 없이 소연을 바라본다. 소연이 찻잔을 만지작거린다.",
            "user_intent": "침묵으로 상대의 반응을 유도",
            "position": {"value": 0.5, "reason": "중립적 위치, 정보 비대칭은 소연 쪽"},
            "effect": {"value": 0.4, "reason": "침묵의 압박, 그러나 직접적 행동 없음"},
            "energy_direction": "rising",
            "aspects": ["창밖의 빗소리", "식어가는 차", "소연의 손끝 떨림"],
            "relevant_npcs": ["소연"],
            "psyche_states": {
                "소연": {
                    "psyche": {"descriptor": "불안한 시선 회피, 입술을 깨문다", "value": -25, "primary_emotion": "apprehension",
                               "active_needs": ["safety", "intimacy"], "self_opacity": "claims everything is fine — actual: guilt over hidden secret",
                               "decision_mode": "reactive", "coping": "avoidant"},
                    "soma": {"descriptor": "찻잔을 쥔 손가락 미세한 떨림, 호흡 얕아짐", "polyvagal": "sympathetic",
                             "cultural_affect": "nunchi", "env_influence": "밤의 고요함이 침묵의 무게를 증폭"},
                    "relation": {"descriptor": "유진의 시선을 의식하며 찻잔으로 시선 회피", "value": 30,
                                 "attachment": "anxious", "phase": "identification",
                                 "logos_layer": "membrane thinning — silence is eroding pretense, monolithic guilt surfacing",
                                 "value_conflict": "loyalty vs self-protection — leaning toward avoidance",
                                 "stage": "front"},
                    "deep_read": "Surface: bright smile, forced normalcy. Adaptation: deflection through small talk and busy hands. Core: desperate need to be forgiven before confession. Lack: belief that truth will not be survived by the relationship."
                },
            },
            "narrative_chain": {"chain_status": "OPEN", "topic_lock": None, "conclusion_proximity": 15, "open_threads": ["Mystery: 소연이 숨기는 것"], "silence_type": "heavy"},
            "narrative_hook": "소연의 휴대폰에 알림이 울린다 — 두 사람 모두 화면을 본다",
            "quality_flags": {"convergence_warning": False, "echo_warning": False, "stagnation_warning": False, "mse_deviation": False, "dissonance_flag": False, "redemption_warning": False},
            "npc_attitudes": {
                "소연": {"attitude": "friendly", "trajectory": "declining", "reason": "유진의 침묵이 죄책감을 자극"},
            },
            "npc_knowledge": {
                "소연": {"knows": ["유진이 뭔가 눈치챘을 수 있다"], "secrets_held": ["3일 전 사건의 진상"], "would_share": False, "leak_risk": "medium", "false_beliefs": ["유진은 아직 모를 것이다"]},
            },
            "memory_triggers": [{"trigger": "이 카페에서 처음 만났던 날", "character": "소연", "echo": "그때는 이렇게 무거운 침묵이 아니었다", "type": "nostalgic"}],
        },
    },

    # ----------------------------------------------------------
    "대화": {
        "desc": "NPC와 일상 대화. Peplau/Logos/Attachment 추적.",
        "world": {"doom": 10, "time_slot": "오후", "day": 8},
        "pc": {
            "mask": "하루",
            "passives": [{"name": "요리 달인", "tags": ["Craft"], "desc": "어떤 재료든 맛있게 만든다"}],
            "status_effects": [],
            "ai_memory": {"vigor": {"value": 85}, "composure": {"value": 80}},
        },
        "npcs": {
            "묘조": {"description": "가게 앞에 눌러앉은 고양이. 하루에게만 배를 보여준다.", "source": "session"},
            "소라": {"description": "옆집 카페 주인. 밝고 수다스럽다. 최근 매출 걱정.", "source": "lore"},
        },
        "npc_attitudes": {
            "소라": {"attitude": "friendly", "trajectory": "warming", "depth": 35, "tension": 0},
        },
        "user_input": "소라에게 새로 만든 케이크를 건네며 요즘 어때? 하고 묻는다",
        "dai": {
            "input_analysis": {"Original": "소라에게 새로 만든 케이크를 건네며 요즘 어때? 하고 묻는다", "Enhanced": "하루가 직접 만든 케이크를 건네며 안부를 묻는다", "Plausibility": "High", "Momentum": "Open"},
            "observation": "케이크를 내밀자 소라의 눈이 커진다. 묘조가 발밑에서 올려다본다.",
            "user_intent": "안부 확인 + 선물로 관계 강화",
            "position": {"value": 0.7, "reason": "평화로운 상황, 호의적 관계, 선물이라는 자산"},
            "effect": {"value": 0.3, "reason": "일상적 상호작용, 극적 변화 가능성 낮음"},
            "energy_direction": "rising",
            "aspects": ["버터크림 향", "카페 앞 햇살", "묘조의 꼬리 흔들림"],
            "relevant_npcs": ["소라", "묘조"],
            "psyche_states": {
                "소라": {
                    "psyche": {"descriptor": "예상치 못한 선물에 눈이 커지고 밝아진다", "value": 40, "primary_emotion": "joy",
                               "active_needs": ["belonging", "esteem"], "self_opacity": None,
                               "decision_mode": "reactive", "coping": None},
                    "soma": {"descriptor": "양손으로 케이크를 받으며 미소, 어깨 이완", "polyvagal": "ventral",
                             "cultural_affect": "jeong", "env_influence": None},
                    "relation": {"descriptor": "케이크를 받고 진심으로 고마워하는 표정", "value": 45,
                                 "attachment": "secure", "phase": "identification",
                                 "logos_layer": "transient warmth spike — membrane slightly thinner, genuine gratitude breaking social script",
                                 "value_conflict": None, "stage": "front"},
                    "deep_read": "Surface: cheerful cafe owner persona. Adaptation: social energy as currency for connection. Core: fear of being a burden when things go wrong. Lack: never learned to ask for help directly."
                },
            },
            "narrative_chain": {"chain_status": "OPEN", "topic_lock": None, "conclusion_proximity": 5, "open_threads": ["Desire: 소라의 매출 걱정"], "silence_type": None},
            "narrative_hook": "소라가 케이크를 한 입 먹고 잠시 멈춘다 — 표정이 복잡해진다",
            "quality_flags": {"convergence_warning": False, "echo_warning": False, "stagnation_warning": False, "mse_deviation": False, "dissonance_flag": False, "redemption_warning": False},
            "npc_attitudes": {
                "소라": {"attitude": "grateful", "trajectory": "warming", "reason": "진심이 담긴 선물"},
            },
            "npc_knowledge": {
                "소라": {"knows": ["하루가 요리를 잘함", "케이크를 직접 만듦"], "secrets_held": ["카페 매출 심각"], "would_share": False, "leak_risk": "low", "false_beliefs": []},
            },
            "memory_triggers": [{"trigger": "버터크림 향", "character": "소라", "echo": "엄마가 생일마다 만들어주던 케이크", "type": "nostalgic"}],
        },
    },

    # ----------------------------------------------------------
    "탐색": {
        "desc": "미지의 장소 탐색. 감각 묘사 + 공간 서사.",
        "world": {"doom": 45, "time_slot": "새벽", "day": 6},
        "pc": {
            "mask": "민수",
            "passives": [],
            "status_effects": [],
            "ai_memory": {"vigor": {"value": 55}, "composure": {"value": 60}},
        },
        "npcs": {},
        "npc_attitudes": {},
        "user_input": "손전등을 켜고 복도 끝으로 걸어간다",
        "dai": {
            "input_analysis": {"Original": "손전등을 켜고 복도 끝으로 걸어간다", "Enhanced": "민수가 떨리는 손으로 손전등을 켜고 복도 끝을 향해 천천히 걸어간다", "Plausibility": "High", "Momentum": "Open"},
            "observation": "손전등 빛이 복도 끝을 향한다. 먼지가 빛 속에서 떠다닌다.",
            "user_intent": "복도 끝 정체 확인",
            "position": {"value": 0.3, "reason": "미지의 공간, 혼자, 새벽"},
            "effect": {"value": 0.5, "reason": "탐색 결과에 따라 상황 변화 가능"},
            "energy_direction": "rising",
            "aspects": ["손전등의 좁은 빛", "새벽의 정적", "복도 벽의 균열", "어딘가에서 나는 물소리"],
            "relevant_npcs": [],
            "psyche_states": {
                "민수": {
                    "psyche": {"descriptor": "경계와 호기심이 공존, 입술이 바짝 마른다", "value": -15, "primary_emotion": "anticipation",
                               "active_needs": ["safety"], "self_opacity": None,
                               "decision_mode": "deliberate", "coping": "problem_focused"},
                    "soma": {"descriptor": "어깨 경직, 발소리를 줄이려는 보폭, 입 호흡", "polyvagal": "sympathetic",
                             "cultural_affect": None, "env_influence": "폐쇄된 공간과 어둠이 경계감을 증폭"},
                    "relation": {"descriptor": "혼자", "value": 0,
                                 "attachment": "secure", "phase": "orientation",
                                 "logos_layer": "monolithic layer steady — trained composure holding",
                                 "value_conflict": None, "stage": "back"},
                    "deep_read": "Surface: careful investigator. Adaptation: methodical approach to danger. Core: needs to understand to feel safe. Lack: cannot accept that some things resist understanding."
                },
            },
            "narrative_chain": {"chain_status": "OPEN", "topic_lock": "복도 탐색", "conclusion_proximity": 20, "open_threads": ["Mystery: 복도 끝에 뭐가 있는가", "Threat: 물소리의 정체"], "silence_type": "tense"},
            "narrative_hook": "복도 끝에 문이 보인다 — 살짝 열려 있고 빛이 새어나온다",
            "quality_flags": {"convergence_warning": False, "echo_warning": False, "stagnation_warning": False, "mse_deviation": False, "dissonance_flag": False, "redemption_warning": False},
            "npc_attitudes": {},
            "npc_knowledge": {},
            "memory_triggers": [{"trigger": "좁은 복도의 압박감", "character": "민수", "echo": "어렸을 때 엘리베이터에 갇혔던 기억", "type": "traumatic"}],
        },
    },

    # ----------------------------------------------------------
    "갈등": {
        "desc": "NPC와 가치관 충돌. Dissonance/Value Conflict/Moral Disengagement.",
        "world": {"doom": 50, "time_slot": "오후", "day": 15},
        "pc": {
            "mask": "카이",
            "passives": [{"name": "의지", "tags": ["Mental"], "desc": "위기에도 흔들리지 않는 마음"}],
            "status_effects": [],
            "ai_memory": {"vigor": {"value": 45}, "composure": {"value": 50}},
        },
        "npcs": {
            "동혁": {"description": "카이의 동료. 냉소적이고 합리적. 최근 조직의 비밀 임무에 가담했다. 자신은 올바르다고 확신.", "source": "lore"},
        },
        "npc_attitudes": {
            "동혁": {"attitude": "unfriendly", "trajectory": "declining", "depth": 55, "tension": 70},
        },
        "user_input": "동혁에게 왜 그런 선택을 했냐고 묻는다",
        "dai": {
            "input_analysis": {"Original": "동혁에게 왜 그런 선택을 했냐고 묻는다", "Enhanced": "카이가 동혁의 눈을 보며 선택의 이유를 묻는다", "Plausibility": "High", "Momentum": "Open"},
            "observation": "카이가 묻자 동혁의 턱이 굳어진다. 팔짱을 낀다.",
            "user_intent": "동혁의 동기 확인 + 설득 가능성 탐색",
            "position": {"value": 0.4, "reason": "정보 열위, 그러나 도덕적 고지"},
            "effect": {"value": 0.6, "reason": "관계 방향이 이 대화에서 결정될 수 있음"},
            "energy_direction": "rising",
            "aspects": ["닫힌 사무실 문", "형광등의 윙 소리", "동혁의 팔짱"],
            "relevant_npcs": ["동혁"],
            "psyche_states": {
                "동혁": {
                    "psyche": {"descriptor": "방어적 냉소, 그러나 시선이 흔들린다", "value": -35, "primary_emotion": "contempt",
                               "active_needs": ["esteem", "autonomy"], "self_opacity": "claims rational pragmatism — actual: guilt displacement through moral disengagement",
                               "decision_mode": "deliberate", "coping": "problem_focused"},
                    "soma": {"descriptor": "팔짱, 턱 경직, 호흡은 안정적이나 목에 힘", "polyvagal": "ventral",
                             "cultural_affect": "chaemyeon", "env_influence": None},
                    "relation": {"descriptor": "카이의 질문에 방어벽을 올리면서도 과거 우정의 잔상이 비친다", "value": -15,
                                 "attachment": "avoidant", "phase": "exploitation",
                                 "logos_layer": "monolithic justification system active — 'necessary evil' narrative. membrane sealed. but old loyalty = hairline crack",
                                 "value_conflict": "loyalty to Kai vs commitment to organization — leaning organization but wavering",
                                 "stage": "front"},
                    "deep_read": "Surface: cold pragmatist. Adaptation: moral disengagement as survival tool. Core: needs to believe his choices were the only option. Lack: cannot face the possibility that he chose wrong freely."
                },
            },
            "narrative_chain": {"chain_status": "OPEN", "topic_lock": "동혁의 선택", "conclusion_proximity": 35, "open_threads": ["Interpersonal: 우정의 마지막 기회인가", "Mystery: 조직의 진짜 목적"], "silence_type": "tense"},
            "narrative_hook": "동혁이 한마디 더 하려다 멈춘다 — 누군가 복도에서 다가오는 발소리",
            "quality_flags": {"convergence_warning": False, "echo_warning": False, "stagnation_warning": False, "mse_deviation": False, "dissonance_flag": True, "redemption_warning": False},
            "npc_attitudes": {
                "동혁": {"attitude": "unfriendly", "trajectory": "declining", "reason": "도덕적 질문이 체면을 위협"},
            },
            "npc_knowledge": {
                "동혁": {"knows": ["카이가 진실을 의심하고 있다"], "secrets_held": ["조직 임무의 실제 내용", "3명이 피해를 입었다는 사실"], "would_share": False, "leak_risk": "medium", "false_beliefs": ["카이는 증거가 없을 것이다", "자신의 선택은 정당했다"]},
            },
            "memory_triggers": [{"trigger": "함께 훈련하던 시절", "character": "동혁", "echo": "카이와 등을 맞대고 싸우던 그때", "type": "nostalgic"}],
        },
    },
}


# =========================================================
# 헬퍼
# =========================================================
def print_header(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def setup_scenario(name):
    """시나리오 데이터를 domain_manager에 로드"""
    s = SCENARIOS[name]
    cid = CHANNEL

    mock_dm._domains.pop(cid, None)
    mock_dm._ensure(cid)

    domain_manager.update_world_state(cid, s["world"].copy())

    pc = s["pc"].copy()
    pc["status"] = "active"
    domain_manager.save_participant_data(cid, USER_ID, pc)

    for npc_name, npc_data in s.get("npcs", {}).items():
        npc_manager.update_npc(cid, npc_name, npc_data.copy())

    if s.get("npc_attitudes"):
        domain_manager.set_npc_attitudes(cid, s["npc_attitudes"].copy())

    domain_manager.append_history(cid, "User", "(세션 시작)")
    domain_manager.append_history(cid, "Model", "세계가 펼쳐진다.")

    return s


# =========================================================
# 테스트: 프롬프트 빌드 + DAI 주입 검증
# =========================================================
def test_prompt_build(scenario_name, show_prompt=False, show_slot=None):
    """slot_manager로 34슬롯 프롬프트를 빌드하고 DAI 주입 확인"""
    print_header(f"[{scenario_name}] 프롬프트 빌드")
    s = setup_scenario(scenario_name)
    cid = CHANNEL
    dai = s.get("dai", {})

    class MockCtx:
        pass

    ctx = MockCtx()
    ctx.channel_id = cid
    ctx.user_id = USER_ID
    ctx.user_mask = s["pc"]["mask"]
    ctx.action_text = s.get("user_input", "")
    ctx.domain_data = mock_dm.get_domain(cid)
    ctx.player_data = domain_manager.get_participant_data(cid, USER_ID)
    ctx.dai = dai
    ctx.active_genres = None
    ctx.custom_tone = None
    ctx.scene_type = dai.get("scene_type", "normal")
    ctx.lore_txt = domain_manager.get_lore(cid)
    ctx.fermented_summary_text = domain_manager.get_fermented_summary(cid)
    ctx.deep_memory_data = {}
    ctx.world_ctx = f"Day {s['world'].get('day')}, {s['world'].get('time_slot')}, Doom={s['world'].get('doom')}"
    ctx.hist_text = ""

    history = domain_manager.get_history(cid)
    ctx.smart_history = [{"role": h["role"], "content": h["content"]} for h in history]

    try:
        prompt = slot_manager.build_34_step_prompt(ctx)
        prompt_len = len(prompt)
        line_count = prompt.count('\n')
        est_tokens = prompt_len // 2

        print(f"  빌드 성공: {prompt_len} chars, ~{line_count} lines, ~{est_tokens} tokens (추정)")

        # 주요 섹션 존재 확인
        checks = {
            "AI_CORE_IDENTITY": "THEORIA — World Engine" in prompt or "MASTER REFERENCE" in prompt,
            "MIRROR_WORKSHOP": "Mirror_Workshop" in prompt or "MIRROR" in prompt,
            "PHYSICAL_RENDERING": "Physical_Rendering" in prompt or "Camera Eye" in prompt,
            "PC_AUTONOMY": "PC_Autonomy" in prompt or "HARD BAN" in prompt,
            "TELESCOPE": "PRE-OUTPUT QUALITY GATE" in prompt or "┣" in prompt,
            "NPC_BEHAVIOR": "NPC_Behavior" in prompt or "NPC AUTONOMY" in prompt,
            "Input_Analysis": "Input_Analysis" in prompt,
            "User_Input": s["user_input"][:20] in prompt,
        }

        # v2.1 DAI 필드 확인
        dai_checks = {}
        if dai.get("psyche_states"):
            dai_checks["Psyche States (Μ[)"] = "Μ[" in prompt
        if dai.get("narrative_hook"):
            dai_checks["Narrative Hook"] = dai["narrative_hook"][:15] in prompt
        if dai.get("memory_triggers"):
            mt = dai["memory_triggers"][0].get("trigger", "")[:10]
            dai_checks["Memory Trigger"] = mt in prompt if mt else True
        if dai.get("energy_direction"):
            ed = dai["energy_direction"].upper()
            dai_checks[f"Energy Direction: {ed}"] = ed in prompt

        # v2.1 신규 필드 확인
        for char_name, state in dai.get("psyche_states", {}).items():
            if state.get("deep_read"):
                dai_checks["deep_read 주입"] = state["deep_read"][:20] in prompt
                break
        chain = dai.get("narrative_chain", {})
        if chain.get("silence_type"):
            dai_checks["silence_type 주입"] = chain["silence_type"] in prompt

        # 출력
        all_pass = True
        for section, found in checks.items():
            status = "✅" if found else "❌"
            if not found:
                all_pass = False
            print(f"    {status} {section}")

        for field, found in dai_checks.items():
            status = "✅" if found else "❌"
            if not found:
                all_pass = False
            print(f"    {status} {field}")

        # 프롬프트 출력 (--prompt 플래그)
        if show_prompt:
            if show_slot is not None:
                print(f"\n  ※ 슬롯 {show_slot} 개별 출력:")
                static_builder = slot_manager.SlotPromptBuilder()
                static_builder.populate_static_slots()
                slot_content = static_builder.get_slot(show_slot)
                if slot_content:
                    print(f"  --- Slot {show_slot} ({len(slot_content)} chars) ---")
                    print(slot_content[:500])
                    if len(slot_content) > 500:
                        print(f"  ... ({len(slot_content) - 500} chars more)")
                else:
                    print(f"  --- Slot {show_slot}: (비어있음 — 동적 슬롯일 수 있음) ---")
            else:
                print(f"\n{'─'*60}")
                print(f"  전체 프롬프트 ({prompt_len} chars):")
                print(f"{'─'*60}")
                print(prompt[:3000])
                if prompt_len > 3000:
                    print(f"\n  ... ({prompt_len - 3000} chars 생략)")

        return all_pass

    except Exception as e:
        print(f"  ❌ 프롬프트 빌드 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


# =========================================================
# 테스트: DAI v2.1 스키마 무결성
# =========================================================
def test_dai_schema(scenario_name):
    """DAI가 v2.1 psyche 스키마를 올바르게 사용하는지 검증"""
    print_header(f"[{scenario_name}] DAI v2.1 스키마 검증")
    s = SCENARIOS[scenario_name]
    dai = s.get("dai", {})
    passed = 0
    failed = 0

    def check(name, condition):
        nonlocal passed, failed
        if condition:
            print(f"    ✅ {name}")
            passed += 1
        else:
            print(f"    ❌ {name}")
            failed += 1

    # psyche_states 구조
    ps = dai.get("psyche_states", {})
    check("psyche_states 존재", bool(ps))

    for char_name, state in ps.items():
        # psyche (not mental)
        psyche = state.get("psyche", {})
        check(f"{char_name}: psyche.descriptor", bool(psyche.get("descriptor")))
        check(f"{char_name}: psyche.primary_emotion", bool(psyche.get("primary_emotion")))
        check(f"{char_name}: psyche.active_needs", isinstance(psyche.get("active_needs"), list))
        check(f"{char_name}: psyche.decision_mode", psyche.get("decision_mode") in ("reactive", "deliberate"))
        check(f"{char_name}: psyche.coping", psyche.get("coping") in ("problem_focused", "emotion_focused", "avoidant", None))

        # soma
        soma = state.get("soma", {})
        check(f"{char_name}: soma.descriptor", bool(soma.get("descriptor")))
        check(f"{char_name}: soma.polyvagal", soma.get("polyvagal") in ("ventral", "sympathetic", "dorsal"))

        # relation
        rel = state.get("relation", {})
        check(f"{char_name}: relation.attachment", rel.get("attachment") in ("secure", "anxious", "avoidant", "disorganized"))
        check(f"{char_name}: relation.phase", rel.get("phase") in ("orientation", "identification", "exploitation", "resolution"))
        check(f"{char_name}: relation.logos_layer", bool(rel.get("logos_layer")))
        check(f"{char_name}: relation.stage", rel.get("stage") in ("front", "back"))

        # deep_read
        check(f"{char_name}: deep_read", bool(state.get("deep_read")))

    # narrative_chain
    chain = dai.get("narrative_chain", {})
    check("narrative_chain.chain_status", chain.get("chain_status") in ("OPEN", "CLOSED", "DORMANT"))
    check("narrative_chain.open_threads", isinstance(chain.get("open_threads"), list))

    # quality_flags
    qf = dai.get("quality_flags", {})
    check("quality_flags.redemption_warning 존재", "redemption_warning" in qf)

    # npc_knowledge false_beliefs
    nk = dai.get("npc_knowledge", {})
    for npc_name, kn in nk.items():
        check(f"{npc_name}: false_beliefs 존재", "false_beliefs" in kn)

    print(f"\n  결과: {passed} passed, {failed} failed")
    return failed == 0


# =========================================================
# MAIN
# =========================================================
def main():
    parser = argparse.ArgumentParser(description="Lorekeeper 시나리오 시뮬레이션 (v2.1 — 분석/서사)")
    parser.add_argument("--scenario", "-s", type=str, help="특정 시나리오만 실행")
    parser.add_argument("--list", "-l", action="store_true", help="시나리오 목록")
    parser.add_argument("--prompt", "-p", action="store_true", help="프롬프트 출력")
    parser.add_argument("--slot", type=int, help="특정 슬롯만 출력 (--prompt와 함께)")
    args = parser.parse_args()

    if args.list:
        print("\n사용 가능한 시나리오:")
        for name, s in SCENARIOS.items():
            print(f"  {name}: {s['desc']}")
        return

    scenarios = [args.scenario] if args.scenario else list(SCENARIOS.keys())

    for name in scenarios:
        if name not in SCENARIOS:
            print(f"❌ 시나리오 '{name}'을 찾을 수 없음. --list로 목록 확인.")
            return

    print("\n" + "█"*60)
    print("  LOREKEEPER — SCENARIO SIMULATION v2.1")
    print(f"  시나리오: {', '.join(scenarios)}")
    print(f"  테스트: DAI 스키마 검증 + 프롬프트 빌드")
    print("█"*60)

    passed = 0
    failed = 0

    for name in scenarios:
        print(f"\n{'╔'+'═'*58+'╗'}")
        print(f"{'║'} {name} — {SCENARIOS[name]['desc'][:48]}{'║'}")
        print(f"{'╚'+'═'*58+'╝'}")

        try:
            if test_dai_schema(name):
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ❌ DAI 스키마 검증 실패: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

        try:
            if test_prompt_build(name, show_prompt=args.prompt, show_slot=args.slot):
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ❌ 프롬프트 빌드 실패: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"  SIMULATION COMPLETE: {passed} passed, {failed} failed")
    print(f"  시나리오 {len(scenarios)}개 × 테스트 2종 = {len(scenarios)*2} 테스트")
    print(f"{'='*60}")

    if failed > 0:
        print("❌ SOME TESTS FAILED")
        exit(1)
    else:
        print("✅ ALL TESTS PASSED")


if __name__ == "__main__":
    main()
