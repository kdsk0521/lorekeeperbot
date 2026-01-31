"""
Lorekeeper TRPG Bot - Cache Manager
Thread-safe 캐시 관리 시스템

동시성 안전을 위한 중앙 집중식 캐시 관리.
- RLock 기반 동기화
- 데이터 복사본 반환으로 mutation 방지
- 명시적 무효화 메커니즘
"""

import threading
import copy
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class CacheManager:
    """Thread-safe 캐시 매니저"""

    def __init__(self):
        self._lock = threading.RLock()

        # 캐시 저장소
        self._session_cache: Dict[str, Dict[str, Any]] = {}
        self._lore_cache: Dict[str, str] = {}
        self._lore_original_cache: Dict[str, str] = {}
        self._rules_cache: Dict[str, str] = {}

        # 캐시 히트/미스 통계 (디버깅용)
        self._stats = {
            "session_hits": 0,
            "session_misses": 0,
            "lore_hits": 0,
            "lore_misses": 0,
        }

    # =========================================================
    # Session Cache
    # =========================================================

    def get_session(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """
        세션 캐시에서 데이터 조회.
        캐시 히트 시 deep copy 반환 (mutation 방지).
        """
        with self._lock:
            if channel_id in self._session_cache:
                self._stats["session_hits"] += 1
                # Deep copy로 원본 보호
                return copy.deepcopy(self._session_cache[channel_id])
            self._stats["session_misses"] += 1
            return None

    def set_session(self, channel_id: str, data: Dict[str, Any]) -> None:
        """세션 데이터를 캐시에 저장 (deep copy로 저장)."""
        with self._lock:
            self._session_cache[channel_id] = copy.deepcopy(data)
            logger.debug(f"[Cache] 세션 캐시 업데이트: {channel_id}")

    def invalidate_session(self, channel_id: str) -> None:
        """특정 채널의 세션 캐시 무효화."""
        with self._lock:
            if channel_id in self._session_cache:
                del self._session_cache[channel_id]
                logger.debug(f"[Cache] 세션 캐시 무효화: {channel_id}")

    def has_session(self, channel_id: str) -> bool:
        """세션 캐시 존재 여부 확인."""
        with self._lock:
            return channel_id in self._session_cache

    # =========================================================
    # Lore Cache
    # =========================================================

    def get_lore(self, channel_id: str) -> Optional[str]:
        """로어 캐시 조회."""
        with self._lock:
            if channel_id in self._lore_cache:
                self._stats["lore_hits"] += 1
                return self._lore_cache[channel_id]
            self._stats["lore_misses"] += 1
            return None

    def set_lore(self, channel_id: str, text: str) -> None:
        """로어 캐시 저장."""
        with self._lock:
            self._lore_cache[channel_id] = text

    def invalidate_lore(self, channel_id: str) -> None:
        """로어 캐시 무효화."""
        with self._lock:
            self._lore_cache.pop(channel_id, None)

    # =========================================================
    # Lore Original Cache
    # =========================================================

    def get_lore_original(self, channel_id: str) -> Optional[str]:
        """원본 로어 캐시 조회."""
        with self._lock:
            return self._lore_original_cache.get(channel_id)

    def set_lore_original(self, channel_id: str, text: str) -> None:
        """원본 로어 캐시 저장."""
        with self._lock:
            self._lore_original_cache[channel_id] = text

    def invalidate_lore_original(self, channel_id: str) -> None:
        """원본 로어 캐시 무효화."""
        with self._lock:
            self._lore_original_cache.pop(channel_id, None)

    # =========================================================
    # Rules Cache
    # =========================================================

    def get_rules(self, channel_id: str) -> Optional[str]:
        """룰 캐시 조회."""
        with self._lock:
            return self._rules_cache.get(channel_id)

    def set_rules(self, channel_id: str, text: str) -> None:
        """룰 캐시 저장."""
        with self._lock:
            self._rules_cache[channel_id] = text

    def invalidate_rules(self, channel_id: str) -> None:
        """룰 캐시 무효화."""
        with self._lock:
            self._rules_cache.pop(channel_id, None)

    # =========================================================
    # Bulk Operations
    # =========================================================

    def invalidate_all(self, channel_id: str) -> None:
        """특정 채널의 모든 캐시 무효화."""
        with self._lock:
            self._session_cache.pop(channel_id, None)
            self._lore_cache.pop(channel_id, None)
            self._lore_original_cache.pop(channel_id, None)
            self._rules_cache.pop(channel_id, None)
            logger.debug(f"[Cache] 전체 캐시 무효화: {channel_id}")

    def clear_all(self) -> None:
        """모든 캐시 클리어 (주의해서 사용)."""
        with self._lock:
            self._session_cache.clear()
            self._lore_cache.clear()
            self._lore_original_cache.clear()
            self._rules_cache.clear()
            logger.info("[Cache] 전체 캐시 클리어됨")

    # =========================================================
    # Statistics & Debug
    # =========================================================

    def get_stats(self) -> Dict[str, int]:
        """캐시 통계 조회."""
        with self._lock:
            return {
                **self._stats,
                "session_cache_size": len(self._session_cache),
                "lore_cache_size": len(self._lore_cache),
                "rules_cache_size": len(self._rules_cache),
            }

    def get_cached_channels(self) -> Dict[str, List[str]]:
        """캐시된 채널 목록 조회."""
        with self._lock:
            return {
                "sessions": list(self._session_cache.keys()),
                "lore": list(self._lore_cache.keys()),
                "rules": list(self._rules_cache.keys()),
            }


# 싱글톤 인스턴스 (기존 코드 호환용)
cache = CacheManager()
