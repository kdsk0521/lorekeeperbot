"""
Lorekeeper UNE - Utilities
Helper functions for tag cleaning and prompt formatting.
"""

import re
from typing import List, Dict, Any

def clean_tag(tag: str) -> str:
    """태그에서 불필요한 조사, 특수문자, 관사를 제거하여 정규화합니다."""
    if not tag: return "이변"
    
    # 1. 대괄호 및 특수문자 제거
    clean = re.sub(r'[\[\]\(\)\{\}\*\-\_\+\#]', '', tag).strip()
    
    # 2. 영어 관사 제거 (The, A, An)
    clean = re.sub(r'^(?i)(the|a|an)\s+', '', clean).strip()
    
    # 3. 한국어 조사 제거 (조심스럽게 접근)
    # 태그가 두 단어 이상일 경우 마지막 단어의 조사를 떼는 것은 위험함.
    # 하지만 태그 자체가 '속삭임이' 처럼 들어올 경우를 대비.
    particles = ['이', '가', '은', '는', '을', '를', '으로', '로']
    for p in particles:
        if clean.endswith(p) and len(clean) > len(p) + 1: # 최소 2글자 이상 단어일 때만
            # 예: '속삭임이' -> '속삭임' (단, '나무이' 같은 경우 주의)
            # 사실 태그는 명사형이 좋으므로 과감하게 시도해볼 수 있음.
            pass # 일단 보수적으로 유지
            
    # 4. 최대 길이 제한 (8자)
    if len(clean) > 8:
        clean = clean[:8]
        
    return clean

def format_narrative_anchors(anchors: Dict[str, Any]) -> str:
    """Narrative Anchors를 프롬프트용 텍스트로 변환합니다."""
    msg = []
    if anchors.get("appearance"): msg.append(f"- 외모: {anchors['appearance']}")
    if anchors.get("personality"): msg.append(f"- 성격: {anchors['personality']}")
    if anchors.get("passives"): 
        passives = [p.get("name") if isinstance(p, dict) else str(p) for p in anchors["passives"]]
        msg.append(f"- 보유 특성: {', '.join(passives)}")
    if anchors.get("inventory"):
        msg.append(f"- 주요 소지품: {', '.join(anchors['inventory'])}")
        
    return "\n".join(msg)
