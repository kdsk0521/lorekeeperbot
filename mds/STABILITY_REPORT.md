# Lorekeeperbot 안정성 리포트 (모듈 최소화 관점)

생성일: 2026-02-04
최종 업데이트: 2026-02-04
대상 경로: c:\Users\kdsk\Desktop\lorekeeperbot\lorekeeperbot

---

## 1. 요약
- 치명 순환참조 2건 해결 (main/command_handler/orchestration)
- UNKNOWN 세션 오염 해결
- 레거시 Doom/Anomaly 경로 제거 완료
- DLC OFF 대체값 정책 적용 (Doom=30, Mental=50)
- 이변/적응 파이프라인 연결 및 서사형 시스템 메시지 개선
- 남은 리스크: 대형 순환 그래프 1건(완화 필요), 테스트/로그 안정화

핵심 결론: **신규 모듈을 늘리지 않고도** 치명 순환참조, 데이터 오염, 레거시 로직은 정리 완료. 남은 구조 리스크는 대형 순환 그래프 완화가 핵심.

---

## 2. 치명/고위험 이슈

### 2.1 main ↔ command_handler ↔ orchestration 순환참조
- 경로: `main.py` → `orchestration.py` → `command_handler.py` → `main.py`
- 상태: **해결 완료**
- 조치: `get_orchestration_runtime`로 런타임 단일화, 불필요 import 제거

### 2.2 대형 순환 그래프 (실행 순서 민감)
- 구성: `domain_manager`, `fermentation`, `game_character`, `game_system`, `game_world`, `npc_manager`, `orchestration_context`
- 위험: 단독 실행/테스트 시 부분 초기화 상태 발생 가능
- 상태: **완화 진행**
- 진행 내용: `domain_manager` → `game_system` 직접 의존 제거
- 진행 내용: `convert_to_game_context`, `sync_from_game_context`를 `une_facade.py`로 이동
- 진행 내용: `orchestration_context`의 무거운 import를 함수 내부로 이동
- 진행 내용: `game_system`의 `from X import *` 제거 (명시적 re-export 유지)

---

## 3. 레거시/죽은 로직 정리

### 3.1 Doom/Anomaly 레거시 경로
- 제거 완료: `calculate_doom_increase`, `process_doom_tick`, `should_trigger_anomaly`, `process_abnormal_turn`
- UNE 경로만 유지하여 중복 동작 제거

---

## 4. 데이터 오염 위험

### 4.1 UNKNOWN 세션 생성
- 원인: `update_mental`에서 UNKNOWN으로 저장되는 경로
- 상태: **해결 완료**
- 조치: `channel_id`, `user_id` 전달로 실제 사용자 ID로 저장

---

## 5. 개선 방향 (모듈 추가 최소화)

### 5.1 main 순환참조 제거
- 상태: **완료**
- 조치: orchestration 런타임 단일화, import 방향 수정

### 5.2 대형 순환 그래프 완화
- 상태: **진행 중**
- 완료: `domain_manager.convert_to_game_context`, `sync_from_game_context`를 `une_facade.py`로 이동
- 완료: `domain_manager`에서 `game_system` 의존 제거
- 완료: `orchestration_context` 지연 import 적용
- 완료: `game_system`의 `from X import *` 제거
- 남은 과제: `game_system` ↔ `game_world` 경계 정리 (필요 시)

### 5.3 UNKNOWN 오염 버그 수정
- 상태: **완료**
- 조치: `update_mental` 인자 명시 전달

### 5.4 레거시 로직 정리
- 상태: **완료**
- 조치: UNE 경로 외 중복 로직 제거

### 5.5 DLC OFF 대체값 정책
- 상태: **완료**
- Doom OFF 시 `bus.doom.value = 30`
- Mental OFF 시 `bus.mental.value = 50`
- `active=False` 유지

### 5.6 이변/적응 파이프라인 연결
- 상태: **완료**
- 흐름: Theoria → `anomaly_profile`(tag/category/intensity/polarity/line)
- 흐름: Waterfall → `bus.anomaly` 저장
- 흐름: Anomaly → 적응 판정/피해/보정 처리
- 흐름: Mental → 적응 업데이트 동기화
- 흐름: UNE → 서사형 시스템 메시지 출력
- Doom OFF 시 이변 확률: **30% 고정**

---

## 6. 권장 작업 순서 (잔여)
1. 대형 순환 그래프 완화(구조 안정화)
2. 테스트 파이프라인 안정화(`pytest` 환경/플러그인 자동 로드 문제 정리)
3. 로그/시스템 메시지 톤 보정(필요 시)

---

## 7. 검증 체크리스트
- `python -m py_compile` 문법 체크
- `!retry` 정상 동작
- Doom OFF 상태에서 이변 확률 30% 확인
- 이변 발생 시 서사 1문장 + 적응 결과 라인 출력 확인
- `data/sessions/UNKNOWN.json` 생성 여부 모니터링

---

## 8. 비고
- 본 리포트는 “모듈 수 증가를 억제”하는 방향을 우선 고려한 대안안임.
- 구조적 정합성을 더 강화하려면 추후 타입/브리지 분리를 추가 검토.
