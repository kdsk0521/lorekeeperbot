"""
Lorekeeper UNE - Utilities (묘비, 2026-07-06 감사)

clean_tag / format_narrative_anchors 제거 — 둘 다 호출자 0, 모듈 자체를
임포트하는 곳도 0 (완전 고아 모듈). 태그 정규화는 anomaly_module 내부 로직이,
anchors 조립은 orchestration_context가 attribute 경로로 직접 담당.
새 UNE 공용 유틸이 필요해지면 이 파일을 재사용.
"""
