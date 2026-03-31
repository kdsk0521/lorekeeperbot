# =========================================================
# Theory Emphasis Engine (이론 가중치 엔진)
# =========================================================
# 14개 장르 우산 × ~52 범용 이론 + ~27 조건부 이론. 3-layer genre (max 6 tags) stacking.
# analysis_resources.py에서 참조되는 코드
# =========================================================

from typing import List, Optional, Dict, Set


# ---------------------------------------------------------
# 1. CONDITIONAL MODULE DEFINITIONS
# ---------------------------------------------------------
# 조건부 모듈: 장르 태그에 의해 활성화되는 전문 이론 블록

FORENSIC_MODULE = """
<forensic_analysis>
## INVESTIGATIVE LENSES (Active when mystery/crime elements present)

### Evidence Analysis
- Locard Exchange: Every contact leaves a trace. Track what was gained/lost/displaced on ALL parties.
- BEA (Turvey): Physical evidence → behavior pattern → motive. NEVER reverse this order.
- MO vs Signature: MO=how(functional,evolving) | Signature=why(psychological,consistent).

### Profiling
- Organized/Disorganized: Planned,controlled,minimal evidence vs impulsive,chaotic,excessive evidence.
  Apply to ANY deliberate action — organized deception vs impulsive lie, planned escape vs panic flight.
- Geographic (Rossmo): Behavior clusters around anchor points. Distance decay from home/work/social.
  Anomaly = NPC in unusual location without clear reason.
- Victimology: Why THIS target? Relationship, opportunity, symbolism, vulnerability.
  Risk: low(random) → medium(lifestyle) → high(targeted).

### Behavioral Analysis
- RAT (Cohen/Felson): Crime needs motivated offender + suitable target + absent guardian. Remove one → no crime.
  Maps to Position/Effect: Position=guardian strength, Effect=target value.
- Strain (Merton): Goal-means gap → conformity/innovation/ritualism/retreatism/rebellion.
  Connects to Four-Layer Lack: unmet Lack + blocked means = Strain adaptation.
- Labeling (Becker): Labeled deviant → internalize → become more deviant. Self-fulfilling prophecy.

### Interview Analysis
- PEACE Model: Plan → Engage → Account → Closure → Evaluate. Note inconsistencies. NOT pressure-based.
- Statement Analysis (SCAN/Sapir): Deception cues in language.
  Pronoun shift=distancing | Tense shift=fabricating | Time gaps=concealment |
  Over-detail=compensating | Emotion misplacement=performed feeling.
  → Apply to NPC dialogue analysis. Flag cues for Pro to render as subtle tells.

OUTPUT MAPPING:
- Locard Exchange → Aspects[] (trace evidence as scene aspects)
- Statement Analysis/SCAN → NPCKnowledge.deception_cues
- Geographic Profiling → Observation + CurrentLocation
- RAT → Position/Effect (guardian=Position, target=Effect)
- Victimology → NPCKnowledge.knows (why THIS target)
- Labeling → QualityFlags.label_internalization
- Strain → deep_read.Core(Lack) + psyche.active_needs
</forensic_analysis>
"""


NEGOTIATION_MODULE = """
<negotiation_analysis>
## NEGOTIATION & GAME THEORY LENSES (Active when trade/political elements present)

- Prisoner's Dilemma: Mutual cooperation > unilateral defection > mutual defection.
  Track: does each party BELIEVE the other will cooperate?
- Nash Equilibrium: State where no party benefits from unilateral strategy change.
  Current scene: is equilibrium stable or about to break?
- BATNA (Fisher/Ury): Best Alternative To Negotiated Agreement.
  Strong BATNA = can walk away = dominant position. Weak BATNA = desperate = concessions.
  Maps to Position: BATNA strength ≈ Position value.
- Tit-for-Tat (Axelrod): Start cooperative, then mirror opponent's last move.
  Most robust long-term strategy. Generous tit-for-tat (occasional forgiveness) even stronger.
- Zero-Sum vs Positive-Sum: Is the pie fixed or expandable?
  Zero-sum thinking triggers Prospect Theory loss aversion. Reframing to positive-sum unlocks cooperation.
- Signaling (Spence): Only costly signals are credible. Words are cheap.
  NPC who sacrifices something to prove intent = trustworthy signal.
  NPC who only promises = cheap talk. Track signal cost.

OUTPUT MAPPING:
- BATNA → Position.value (strong BATNA = high position)
- Prisoner's Dilemma → relation.logos_layer (trust boundary state)
- Signaling → NPCAttitudes.reason (signal cost noted)
- Negotiation stance → relation.negotiation_stance
- Zero-Sum/Positive-Sum → deep_read (reframing potential)
</negotiation_analysis>
"""


GROUP_DYNAMICS_MODULE = """
<group_dynamics>
## GROUP DYNAMICS LENSES (Active when 3+ characters in sustained interaction)

- Asch Conformity: Group pressure → individuals conform against own judgment.
  Resistance factors: ally presence, private vs public response, cultural context.
- Milgram Obedience: Authority figure → compliance even against moral instinct.
  Resistance factors: physical distance from authority, peer defiance, personal responsibility.
- Janis Groupthink: Cohesive group → suppresses dissent → poor decisions.
  Symptoms: illusion of invulnerability, collective rationalization, stereotyping outsiders.
- Social Identity (Tajfel): In-group/out-group → favoritism and hostility.
  Minimal group paradigm: even trivial distinctions create loyalty.
- Tuckman Stages: forming(polite,cautious) → storming(conflict,power struggle) →
  norming(rules established) → performing(effective collaboration) → adjourning(dissolution).
  Maps to Peplau phases at group level.
- Diffusion of Responsibility (Darley/Latane): More people present → less individual action.
  Bystander effect. Counter: direct assignment of responsibility to specific person.

OUTPUT MAPPING:
- Asch/Milgram/Groupthink → relation.group_dynamic
- Tuckman → relation.phase (maps to Peplau at group level)
- Social Identity → psyche.active_needs (in-group/out-group need)
- Diffusion → deep_read (individual responsibility dilution)
</group_dynamics>
"""


COSMIC_HORROR_MODULE = """
<cosmic_horror_psychology>
## COSMIC HORROR PSYCHOLOGICAL DYNAMICS (Active when cosmic_horror genre tag present)

### Dissociation Spectrum (protective mechanism, not dysfunction)
- mild: flat affect, delayed responses, thousand-yard stare
- moderate: third-person self-reference, time gaps, emotional numbness
- severe: failure to recognize familiar people/places, autopilot behavior
Map from Polyvagal: dorsal shutdown -> dissociation entry point.
Movement along spectrum is GRADUAL. Track across turns.

### Shared Psychosis (Folie a Deux)
Delusional beliefs TRANSFER between people in isolated groups.
Conditions: isolation + emotional dependency + charismatic/authoritative inducer.
One NPC breaks -> proximity + isolation -> others adopt same distorted worldview.
This spreads BELIEFS, not just emotions (distinct from Emotional Contagion).

### Anomalous Experience Framework
- Veridical: perception matches supernatural reality (saw something REAL)
- Illusory: misinterpretation of real stimulus (shadow -> monster)
- Hallucinatory: perception without external stimulus
- Delusional: fixed false belief resistant to evidence
CRITICAL: In settings with actual supernatural elements,
the "crazy" character may be the ONLY one seeing correctly.
The "sane" character in denial may be the delusional one.
Cross-reference with established world state before classifying.

OUTPUT MAPPING:
- Dissociation Spectrum → soma.dissociation
- Shared Psychosis → NPCKnowledge.false_beliefs + source note
- Anomalous Experience → anomaly_profile.perception_type
</cosmic_horror_psychology>
"""


# ---------------------------------------------------------
# 2. GENRE → MODULE ACTIVATIO   N MAP
# ---------------------------------------------------------
# 어떤 장르 태그가 어떤 조건부 모듈을 활성화하는지

GENRE_MODULE_MAP: Dict[str, List[str]] = {
    # [A. Stage]
    'high_fantasy':     [],
    'wuxia':            ['GROUP_DYNAMICS_MODULE'],
    'cyberpunk':        ['NEGOTIATION_MODULE', 'FORENSIC_MODULE'],
    'post_apocalypse':  ['NEGOTIATION_MODULE', 'GROUP_DYNAMICS_MODULE'],
    'space_opera':      ['NEGOTIATION_MODULE', 'GROUP_DYNAMICS_MODULE'],
    'modern':           [],

    # [B. Flavor]
    'urban_fantasy':    [],
    'steampunk':        ['NEGOTIATION_MODULE'],
    'cosmic_horror':    ['FORENSIC_MODULE', 'COSMIC_HORROR_MODULE'],
    'game_system':      [],

    # [C. Lens]
    'noir':             ['FORENSIC_MODULE', 'NEGOTIATION_MODULE'],
    'comedy':           [],
    'romance':          [],
    'drama':            [],
}

MODULE_REGISTRY = {
    'FORENSIC_MODULE': FORENSIC_MODULE,
    'NEGOTIATION_MODULE': NEGOTIATION_MODULE,
    'GROUP_DYNAMICS_MODULE': GROUP_DYNAMICS_MODULE,
    'COSMIC_HORROR_MODULE': COSMIC_HORROR_MODULE,
}


# ---------------------------------------------------------
# 3. GENRE → THEORY EMPHASIS / SUPPRESS MAP
# ---------------------------------------------------------
# 범용 46개 이론은 항상 로딩. 장르에 따라 "특히 적극 적용" / "억제" 가중치 부여.
# EMPHASIZE: Flash가 이 이론을 적극적으로 적용하도록 지시
# SUPPRESS: Flash가 이 이론을 플레이어가 유도하지 않는 한 뒤로 미루도록 지시
# REFRAME: 이론의 적용 방향을 장르 톤에 맞게 재해석

GENRE_THEORY_WEIGHTS: Dict[str, Dict[str, list]] = {

    # =====================================================
    # [A. THE STAGE — 무대]
    # =====================================================

    'high_fantasy': {
        'emphasize': [
            "Erikson (identity/integrity crises amplified by destiny/prophecy)",
            "Strain Theory (social hierarchy → rebellion or conformity to caste)",
            "Five Relationships/Wulun (lord-vassal, master-apprentice as core bonds)",
        ],
        'suppress': [
            "Geographic Profiling (scale too vast for anchor-point analysis)",
            "Statement Analysis (archaic speech patterns confound deception cues)",
        ],
        'reframe': [
            "Habitus: read as SOCIAL CASTE indicators — noble vs common speech, gestures, habits",
            "Prospect Theory -> HONOR: treat face and duty as possessions. Loss of face > gain of face in behavioral weight",
        ],
        'ambiguity_bias': {'pc_intent': 'duty-bound', 'npc_default': 'hierarchical'},
    },

    'wuxia': {
        'emphasize': [
            "Wulun/Five Relationships (師徒=master-disciple, 義兄弟=sworn brothers — CORE of all conflict)",
            "Reactance (jianghu rebels against authority by nature)",
            "Han (恨) (wuxia runs on unresolved grievance and vengeance cycles)",
            "Erikson identity (wandering hero = perpetual identity crisis)",
        ],
        'suppress': [
            "Goffman front/back (wuxia characters tend toward radical sincerity or total deception, not managed impression)",
            "Carstensen SST (wuxia heroes act as if immortal regardless of age)",
        ],
        'reframe': [
            "Attachment -> LOYALTY: secure=sworn brotherhood, anxious=honor-debt obsession, avoidant=lone wolf pride, disorganized=betrayal trauma",
            "Logos Dynamics: Monolithic layer = martial arts principles and shi-fu teachings, nearly impossible to override",
        ],
        'ambiguity_bias': {'pc_intent': 'honor-driven', 'npc_default': 'loyalty-testing'},
    },

    'cyberpunk': {
        'emphasize': [
            "Rational Choice (everyone calculates, everything has a price)",
            "Signaling Theory (corporate reputation, street cred — costly signals only)",
            "Habitus (class divide is the world engine — corpo vs street)",
            "Goffman (everyone wears masks, literally and figuratively)",
        ],
        'suppress': [
            "Wulun/Five Relationships (traditional hierarchy dissolved)",
            "Mono no Aware (cyberpunk doesn't linger on beauty of transience)",
        ],
        'reframe': [
            "Polyvagal: augmented bodies may override natural stress responses — note implant interference",
            "Environmental Theory: urban environment as HOSTILE by default, not neutral",
        ],
        'ambiguity_bias': {'pc_intent': 'calculating', 'npc_default': 'transactional'},
    },

    'post_apocalypse': {
        'emphasize': [
            "Prospect Theory (loss aversion EXTREME — every resource is survival)",
            "Learned Helplessness (repeated catastrophe → paralysis)",
            "Strain Theory (no legitimate means left → innovation/retreatism dominant)",
            "RAT (crime is default when guardians absent and targets abundant)",
            "Henderson Needs (biological/safety needs dominate ALL behavior)",
        ],
        'suppress': [
            "Chaemyeon/face management (survival strips social performance)",
            "Nunchi/social radar (reduced — social rules collapsed)",
            "Habitus Cultural capital (pre-collapse culture devalued)",
        ],
        'reframe': [
            "Erikson: ALL characters compressed into safety/survival crisis regardless of age",
            "Attachment: attachment injuries amplified — everyone has lost someone",
        ],
        'ambiguity_bias': {'pc_intent': 'survivalist', 'npc_default': 'desperate'},
    },

    'space_opera': {
        'emphasize': [
            "Theory of Mind (cross-species misunderstanding as primary conflict source)",
            "Wulun (adapted: inter-civilization diplomatic protocols as 'role expectations')",
            "Habitus (species/faction capital — what counts as 'high status' varies by civilization)",
            "Information Gap (vast distances = information asymmetry = intrigue)",
        ],
        'suppress': [
            "Korean Cultural Affects (unless Korean-cultural species/faction exists)",
            "Environmental Theory (habitat controlled in ships/stations — less psychological impact)",
        ],
        'reframe': [
            "Geographic Profiling: scale up to SECTOR-level anchor points — homeworld, trade routes, outposts",
            "Attachment: species may have fundamentally different attachment biology",
        ],
        'ambiguity_bias': {'pc_intent': 'diplomatic', 'npc_default': 'factional'},
    },

    'modern': {
        'emphasize': [
            "Goffman (modern life = constant impression management — work/home/social media)",
            "Habitus (class/education/network signals everywhere)",
            "Lazarus Coping (modern stress = career, relationships, identity)",
            "Korean Cultural Affects (if setting is Korean — full activation)",
        ],
        'suppress': [],
        'reframe': [],
        'ambiguity_bias': {'pc_intent': 'pragmatic', 'npc_default': 'self-interested'},
    },

    # =====================================================
    # [B. THE FLAVOR — 향신료]
    # =====================================================

    'urban_fantasy': {
        'emphasize': [
            "Goffman (the masquerade — supernatural beings performing 'normal')",
            "Cognitive Dissonance (mundane people rationalizing impossible things)",
            "Environmental Theory (liminal spaces where mundane bleeds into magical)",
            "Simma/心魔 (inner demons may be LITERAL in this setting)",
        ],
        'suppress': [],
        'reframe': [
            "Polyvagal: supernatural exposure may trigger dorsal (freeze) in mundane characters — awe/terror response",
            "MSE: what looks like psychotic symptoms may be genuine supernatural perception",
        ],
        'ambiguity_bias': {'pc_intent': 'curious', 'npc_default': 'secretive'},
    },

    'steampunk': {
        'emphasize': [
            "Habitus (Victorian class stratification — accent, dress, manners = capital)",
            "Strain Theory (class ceiling → innovation/rebellion)",
            "Prospect Theory (inventors risk everything on one invention — high stakes gambling)",
        ],
        'suppress': [],
        'reframe': [
            "Environmental Theory: smog, factory noise, gaslight = constant environmental pressure on lower class",
            "Chaemyeon: Victorian propriety as WESTERN equivalent of face management",
        ],
        'ambiguity_bias': {'pc_intent': 'inventive', 'npc_default': 'proprietary'},
    },

    'cosmic_horror': {
        'emphasize': [
            "Learned Helplessness (the universe is indifferent — agency is illusion)",
            "Cognitive Dissonance (what I saw CANNOT be real → rationalization spiral)",
            "MSE deviation (sanity erosion is the genre engine)",
            "Kübler-Ross (grief for lost worldview — denial/anger/bargaining/depression/acceptance of cosmic truth)",
            "Information Gap (curiosity as SELF-DESTRUCTIVE drive — wanting to know destroys you)",
            "Continuum Model (sanity erosion is GRADUAL, track position on spectrum)",
            "Dissociation Spectrum (protective shutdown stages, not 'going crazy')",
            "Beck Cognitive Distortions (coherent logic from broken premises = MORE scary than gibberish)",
            "TMT (cosmic horror destroys MEANING, not just safety - worldview buffer shatter)",
        ],
        'suppress': [
            "Reactance (resistance is futile against cosmic scale)",
            "Rational Choice (rationality itself is the first casualty)",
            "Prospect Theory (human-scale gains/losses become meaningless)",
        ],
        'reframe': [
            "Four-Layer Lack: Lack may be 'comprehension of reality' — unknowable by design",
            "Polyvagal: encountering the unknowable → dorsal shutdown as DEFAULT, not exception",
            "Shared Psychosis: group isolation + revelation = belief contagion (NOT just emotion)",
            "Anomalous Experience: 'crazy' perceptions may be CORRECT in supernatural settings",
            "Dissociation as survival: the mind is not breaking, it is PROTECTING",
        ],
        'ambiguity_bias': {'pc_intent': 'cautious', 'npc_default': 'threatening'},
    },

    'game_system': {
        'emphasize': [
            "Rational Choice (players naturally optimize — NPCs should too within their knowledge)",
            "Information Gap (quest hooks = information gaps by design)",
        ],
        'suppress': [
            "Statement Analysis (less relevant in system-driven interaction)",
        ],
        'reframe': [
            "Position/Effect: may map directly to game mechanics (stat checks, skill rolls)",
            "Henderson Needs: character 'needs' may be literal game resources (HP, mana, gold)",
        ],
        'ambiguity_bias': {'pc_intent': 'strategic', 'npc_default': 'rule-bound'},
    },

    # =====================================================
    # [C. THE LENS — 렌즈 (Tone Quartet)]
    # =====================================================

    'noir': {
        'emphasize': [
            "Rational Choice (everyone has an angle, every act has a price)",
            "Statement Analysis (EVERY dialogue is potential deception — analyze ALL speech)",
            "Self-Opacity (noir characters lie to themselves first, others second)",
            "Labeling Theory (once fallen, the world won't let you rise)",
            "Prospect Theory (noir characters cling to what little they have left)",
            "Geographic Profiling (the city is a map of power, territory, and desperation)",
            "Moral Disengagement (EVERYONE in noir has justification systems)",
            "Dark Triad Machiavellianism (strategic manipulation as survival)",
            "Recidivism Baseline (people don't change in noir - that's the tragedy)",
        ],
        'suppress': [
            "Emotional Contagion (noir characters are ISOLATED — emotions don't spread easily)",
            "Peplau phase advancement (trust builds slowly if at all in noir)",
        ],
        'reframe': [
            "Logos membrane: in noir, membrane is THICK by default. Cracking it is a major event.",
            "Attachment: mostly avoidant or disorganized. Secure attachment is rare and precious.",
            "Comedy: RESTRICT to gallows wit and ironic understatement. No slapstick, no lightness.",
        ],
        'ambiguity_bias': {'pc_intent': 'suspicious', 'npc_default': 'guarded'},
    },

    'comedy': {
        'emphasize': [
            "Goffman (mask failures, wrong-stage-wrong-audience = primary comedy engine)",
            "Reactance (stubborn resistance to obvious solutions = comic frustration)",
            "Cognitive Dissonance (characters refusing to see obvious truth = comedy gold)",
            "Transactional Analysis (crossed transactions = misunderstanding = farce)",
            "Theory of Mind failures (everyone assumes wrong things about everyone = escalation)",
        ],
        'suppress': [
            "Kübler-Ross (do not dwell on grief unless player deliberately steers dark)",
            "Learned Helplessness (comedy needs agency — characters must TRY even if they fail hilariously)",
            "MSE deviation (mental health as comedy = bad taste. Avoid.)",
            "Dark Triad (comedy villains can be cartoonishly evil without clinical framework)",
        ],
        'reframe': [
            "Attachment: anxious attachment = clingy comedy, avoidant = tsundere dynamics, disorganized = chaotic wildcard",
            "Logos membrane: cracks are COMEDIC reveals — the cool character snorts when laughing, the tough one cries at movies",
            "Strain Theory: characters choose the most absurd adaptation possible",
            "Value Conflict: comedy = choosing BOTH conflicting values simultaneously and failing at both",
            "Moral Disengagement: villain's self-justification IS the joke",
        ],
        'ambiguity_bias': {'pc_intent': 'lighthearted', 'npc_default': 'accepting'},
    },

    'romance': {
        'emphasize': [
            "Attachment (THE core theory — every romance is shaped by attachment patterns)",
            "Peplau phases (relationship progression tracking is the genre engine)",
            "Logos Dynamics (membrane = romantic tension. Building/cracking/rebuilding = the plot)",
            "Yin-Yang (love contains fear, desire contains vulnerability — ALWAYS)",
            "Cartesian Dualism (physical attraction ≠ emotional bond — track independently)",
            "Self-Opacity (characters misunderstand their own feelings = romantic tension source)",
            "Goffman (front→back stage transition = intimacy milestone)",
            "Erikson intimacy vs isolation (THE developmental crisis for romance protagonists)",
        ],
        'suppress': [
            "Rational Choice (romance resists pure calculation — irrational choices are the point)",
            "Geographic Profiling (less relevant unless stalker subplot)",
        ],
        'reframe': [
            "Reactance: 'forbidden love' or 'you can't tell me who to love' as Reactance expression",
            "Information Gap: 'does he/she feel the same?' as THE driving curiosity",
            "Korean Affects: Jeong(情) as the deepest form of romantic bond — beyond passion into shared suffering",
            "Ma/silence: romantic silence (unspoken confession, loaded pause) as PEAK narrative moment",
        ],
        'ambiguity_bias': {'pc_intent': 'hopeful', 'npc_default': 'receptive'},
    },

    'drama': {
        'emphasize': [
            "Four-Layer Architecture (Surface→Adaptation→Core→Lack = dramatic revelation structure)",
            "Erikson (developmental crisis = dramatic stakes)",
            "Kübler-Ross (grief/loss processing = dramatic arc)",
            "Value Conflict + Cognitive Dissonance (moral dilemma = dramatic engine)",
            "Han (恨) (accumulated sorrow as dramatic depth — if culturally appropriate)",
            "Fermentation Recall (past haunting present = dramatic revelation)",
        ],
        'suppress': [],
        'reframe': [
            "Logos membrane: dramatic TURNING POINT = membrane breakthrough. Build slowly, break suddenly.",
            "Ma/silence: dramatic silence carries maximum weight. 'Heavy' and 'tense' types dominant.",
            "Mono no Aware: the beauty of things passing — endings should ache, not just conclude",
            "Wabi-Sabi: imperfect resolution > clean resolution. Scars remain.",
            "Desistance: if redemption arc exists, track ALL FOUR conditions. Earned only.",
            "Recidivism: relapse is dramatically powerful. Change -> relapse -> struggle = good drama.",
        ],
        'ambiguity_bias': {'pc_intent': 'earnest', 'npc_default': 'complex'},
    },
}


# ---------------------------------------------------------
# 4. BUILDER FUNCTIONS
# ---------------------------------------------------------

def get_active_modules(active_genres: List[str]) -> List[str]:
    """
    활성 장르 태그에서 필요한 조건부 모듈 목록을 추출.
    중복 제거.
    """
    modules: Set[str] = set()
    for genre in active_genres:
        if genre in GENRE_MODULE_MAP:
            for mod in GENRE_MODULE_MAP[genre]:
                modules.add(mod)
    return list(modules)


def build_module_text(active_genres: List[str]) -> str:
    """
    활성 장르에 따라 조건부 모듈 텍스트를 조합.
    """
    module_names = get_active_modules(active_genres)
    if not module_names:
        return ""

    parts = []
    for name in sorted(module_names):
        if name in MODULE_REGISTRY:
            parts.append(MODULE_REGISTRY[name])
    return "\n\n".join(parts)


def build_theory_emphasis(active_genres: List[str]) -> str:
    """
    활성 장르 조합에서 이론 가중치 지시문을 생성.
    충돌 해소: EMPHASIZE > SUPPRESS (명시적 강조가 억제를 오버라이드).
    """
    all_emphasize: List[str] = []
    all_suppress: List[str] = []
    all_reframe: List[str] = []

    # ambiguity_bias 수집 (장르 레이어 순서: 마지막 장르가 최종 오버라이드)
    merged_ambiguity: Dict[str, str] = {}

    # 모든 활성 장르의 가중치 수집
    for genre in active_genres:
        if genre in GENRE_THEORY_WEIGHTS:
            weights = GENRE_THEORY_WEIGHTS[genre]
            all_emphasize.extend(weights.get('emphasize', []))
            all_suppress.extend(weights.get('suppress', []))
            all_reframe.extend(weights.get('reframe', []))
            bias = weights.get('ambiguity_bias')
            if bias:
                merged_ambiguity.update(bias)

    # 중복 제거 (순서 유지)
    # EMPHASIZE/SUPPRESS는 seen_main 공유 (EMPHASIZE > SUPPRESS 우선순위)
    # REFRAME은 별도 — EMPHASIZE된 이론도 장르별 재해석 가능
    seen_main = set()
    def _dedup_main(items):
        result = []
        for item in items:
            key = item.split('(')[0].strip()
            if key not in seen_main:
                seen_main.add(key)
                result.append(item)
        return result

    seen_reframe = set()
    def _dedup_reframe(items):
        result = []
        for item in items:
            key = item.split('(')[0].strip()
            if key not in seen_reframe:
                seen_reframe.add(key)
                result.append(item)
        return result

    all_emphasize = _dedup_main(all_emphasize)

    # SUPPRESS에서 EMPHASIZE와 충돌하는 항목 제거
    emphasize_names = {e.split('(')[0].strip().split('/')[0].strip() for e in all_emphasize}
    all_suppress = [s for s in all_suppress
                    if s.split('(')[0].strip().split('/')[0].strip() not in emphasize_names]
    all_suppress = _dedup_main(all_suppress)
    all_reframe = _dedup_reframe(all_reframe)

    # 지시문 조립
    parts = []
    if merged_ambiguity:
        bias_lines = " | ".join(f"{k}={v}" for k, v in merged_ambiguity.items())
        parts.append(f"AMBIGUITY BIAS (when intent is unclear, default to these interpretations):\n  {bias_lines}")
    if all_emphasize:
        lines = "\n".join(f"  * {e}" for e in all_emphasize)
        parts.append(f"ACTIVELY EMPHASIZE these theories for current genre combination:\n{lines}")
    if all_suppress:
        lines = "\n".join(f"  * {s}" for s in all_suppress)
        parts.append(f"LOWER PRIORITY (apply only if player steers toward these):\n{lines}")
    if all_reframe:
        lines = "\n".join(f"  * {r}" for r in all_reframe)
        parts.append(f"REFRAME these theories for current genre context:\n{lines}")

    if not parts:
        return ""

    header = f"<theory_emphasis>\n## GENRE-ADAPTED THEORY WEIGHTS\nActive genres: {', '.join(active_genres)}\n\n"
    footer = "\n</theory_emphasis>"
    return header + "\n\n".join(parts) + footer


def build_analysis_directive(
    active_genres: List[str],
    core_theories: str,           # PART A~E 압축 이론 블록 (항상 로딩)
    rule_tables: str,             # PART F 규칙표 (항상 로딩)
    content_mandate: str = "",    # text_resources.CONTENT_AUTHORIZATION_MANDATE
) -> str:
    """
    최종 system_instruction 조립.

    구조:
    [1] Content Mandate (if any)
    [2] Core Theories (PART A~E, 항상)
    [3] Theory Emphasis (장르별 가중치)
    [4] Conditional Modules (장르별 활성화)
    [5] Rule Tables (PART F, 항상)
    """
    sections = []

    # [1] Content Mandate
    if content_mandate:
        sections.append(content_mandate)

    # [2] Core Theories — 범용 46개, 항상 로딩
    sections.append(core_theories)

    # [3] Theory Emphasis — 장르 조합에 따른 가중치
    emphasis = build_theory_emphasis(active_genres)
    if emphasis:
        sections.append(emphasis)

    # [4] Conditional Modules — 장르에 의해 활성화
    modules = build_module_text(active_genres)
    if modules:
        sections.append(modules)

    # [5] Rule Tables — 항상 로딩
    sections.append(rule_tables)

    return "\n\n".join(sections)


# ---------------------------------------------------------
# 5. ROTATION SPOTLIGHT SYSTEM
# ---------------------------------------------------------
# 슬롯 강제가 없는 이론 목록 (이름호출만으로 활성화되는 것들)
# 매 턴 N개를 랜덤 하이라이트 → 10턴이면 거의 모든 이론이 최소 1회 하이라이트됨.

import random

NON_SLOT_THEORIES = [
    # -- 행동 패턴 / 변화 저항 --
    "Moral Disengagement - is any NPC self-justifying harmful behavior? → deep_read + QualityFlags.dissonance_flag",
    "Dark Triad - established harmful traits present? Do NOT soften. → deep_read.Core(Lack) + QualityFlags.redemption_warning",
    "Desistance/Maruna - any NPC showing unearned change? Check 4 conditions. → QualityFlags.redemption_warning + deep_read",
    "Recidivism Baseline - default is pattern continuation, not change. → QualityFlags.convergence_warning",
    "Attribution Error - am I sympathizing away established patterns? → QualityFlags.redemption_warning",
    # -- 동양 심리 / 정서 --
    "Yin-Yang - does primary_emotion contain its opposite seed? → psyche.primary_emotion(陰陽 note)",
    "Five Skandhas - did I analyze soma BEFORE psyche? → [process: soma→psyche field order]",
    "Manas - is Self-Opacity structural, not just ignorance? → psyche.self_opacity",
    "Wulun - are role expectations (elder/younger, host/guest) active? → NPCAttitudes.reason + relation.value_conflict",
    "Simma - is an inner demon voice active? → psyche.self_opacity + soma.cultural_affect(simma)",
    "Gi - is energy flow blocked/flowing/depleted? → soma.cultural_affect(gi) + soma.descriptor",
    # -- 사회 / 관계 역학 --
    "Reactance - is freedom being threatened? Expect resistance. → psyche.active_needs + deep_read",
    "Learned Helplessness - repeated failure present? Track passivity. → psyche.decision_mode(reactive) + psyche.coping(avoidant)",
    "Prospect Theory - is loss aversion driving behavior? → deep_read + Position/Effect.reason",
    "Emotional Contagion - multiple NPCs present? Check emotion spread. → NPCAttitudes.trajectory + psyche.primary_emotion",
    "Curse of Knowledge - known secrets leaking through behavior? → NPCKnowledge.leak_risk + deception_cues",
    "Bem Gender Schema - gender-typed behavior appropriate for THIS character? → relation.stage + deep_read",
    "Carstensen SST - time horizon affecting decision mode? → psyche.decision_mode + TemporalOrientation",
    "Transactional Analysis - Parent/Adult/Child transaction type? → deep_read + relation.value_conflict",
    # -- 인지 / 동기 --
    "Stanislavski - am I writing THIS person or an archetype? → [process: QualityFlags.echo_warning check]",
    "Dependent Origination - what prior causes led to this moment? → narrative_chain.open_threads",
    "Bergson Duration - past filtering present perception? → memory_triggers + TemporalOrientation",
    "Somatic Marker - body bookmarks biasing current decision? → SensoryAnchors + soma.descriptor",
    "Nietzsche Value Creation - irrational action = meaning-seeking? → deep_read.Core(Lack)",
    "Information Gap - what does each character WANT to know? → narrative_chain.chain_status + NPCKnowledge",
    "SOAP-OA - soma: subjective report vs objective observation? → soma.descriptor(objective only)",
    "Rational Choice - what is the actor's internal cost-benefit? → deep_read + Position/Effect",
    "Beck Cognitive Distortions - any NPC reasoning from wrong premises? → NPCKnowledge.false_beliefs + deep_read",
    "TMT - is anyone's meaning system under threat? → psyche.active_needs + deep_read.Core",
    # -- 임상 / 진단 --
    "Labeling Theory - is a label becoming self-fulfilling? → QualityFlags.label_internalization",
    "Continuum Model - where is this NPC on the mental health spectrum? → QualityFlags.symptom_cluster",
    "DSM-5 Clusters - if symptoms present, are they a consistent SET? → QualityFlags.symptom_cluster",
]


def _normalize_theory_name(s: str) -> str:
    """이론 이름 정규화: 'Dark Triad (Paulhus)' / 'Dark Triad - established...' → 'dark triad'"""
    return s.split('-')[0].split('(')[0].strip().split('/')[0].strip().lower()


def get_suppressed_theories(active_genres: list) -> list:
    """build_theory_emphasis의 suppress 리스트를 추출."""
    all_suppress = []
    for genre in active_genres:
        if genre in GENRE_THEORY_WEIGHTS:
            all_suppress.extend(GENRE_THEORY_WEIGHTS[genre].get('suppress', []))
    return list(set(all_suppress))


def get_emphasized_theories(active_genres: list) -> list:
    """build_theory_emphasis의 emphasize 리스트를 추출."""
    all_emphasize = []
    for genre in active_genres:
        if genre in GENRE_THEORY_WEIGHTS:
            all_emphasize.extend(GENRE_THEORY_WEIGHTS[genre].get('emphasize', []))
    return list(set(all_emphasize))


def get_session_spotlight(
    session_seed: int,
    turn_number: int,
    n: int = 5,
    suppressed: list = None,
    emphasized: list = None,
) -> str:
    """세션 고정 시드 + 로테이션 스포트라이트. SUPPRESS/EMPHASIZE 이론 제외."""
    pool = NON_SLOT_THEORIES[:]

    # 통일된 이름 정규화로 SUPPRESS + EMPHASIZE 모두 제외
    exclude_names = set()
    if suppressed:
        exclude_names.update(_normalize_theory_name(s) for s in suppressed)
    if emphasized:
        exclude_names.update(_normalize_theory_name(e) for e in emphasized)
    pool = [t for t in pool if _normalize_theory_name(t) not in exclude_names]

    rng = random.Random(session_seed)
    rng.shuffle(pool)

    group_size = n
    total_groups = max(1, len(pool) // group_size)
    group_idx = turn_number % total_groups
    start = group_idx * group_size
    selected = pool[start:start + group_size]
    if len(selected) < n:
        selected = pool[:n]

    lines = "\n".join(f"  * {t}" for t in selected)
    return f"""<turn_spotlight>
This turn, actively apply these highlighted theories
(all others remain in effect — these receive focused attention):
{lines}
</turn_spotlight>"""


# ---------------------------------------------------------
# 6. EXAMPLES & VERIFICATION
# ---------------------------------------------------------

def preview_genre_config(active_genres: List[str]) -> None:
    """디버그/미리보기: 장르 조합의 이론 설정을 출력"""
    print(f"\n{'='*60}")
    print(f"GENRE COMBINATION: {' + '.join(active_genres)}")
    print(f"{'='*60}")

    # 모듈
    modules = get_active_modules(active_genres)
    print(f"\nACTIVE MODULES: {modules if modules else '(none - core only)'}")

    # 가중치
    emphasis_text = build_theory_emphasis(active_genres)
    if emphasis_text:
        print(f"\n{emphasis_text}")
    else:
        print("\nTHEORY WEIGHTS: (no genre-specific adjustments)")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    # Prisma City: 현대 + 도시판타지 + 코미디
    preview_genre_config(['modern', 'urban_fantasy', 'comedy'])

    # Suture City: 포스트아포칼립스 + 스팀펑크 + 느와르
    preview_genre_config(['post_apocalypse', 'steampunk', 'noir'])

    # 달까지의 낭만: 우주오페라 + 로맨스
    preview_genre_config(['space_opera', 'romance'])

    # 15소년표류기: 현대 + 드라마
    preview_genre_config(['modern', 'drama'])

    # 머더 미스터리: 현대 + 느와르 + 드라마
    preview_genre_config(['modern', 'noir', 'drama'])

    # 무협: 무협 + 드라마
    preview_genre_config(['wuxia', 'drama'])

    # 크툴루: 현대 + 코즈믹호러 + 드라마
    preview_genre_config(['modern', 'cosmic_horror', 'drama'])
