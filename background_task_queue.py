"""
Lorekeeper TRPG Bot - Background Task Queue System
채널별 백그라운드 작업을 순차적으로 처리하는 큐 시스템입니다.

주요 기능:
- 채널별 독립적인 작업 큐 관리
- 파일 쓰기 작업의 순차 실행 보장
- 비동기 작업의 안전한 처리
"""

import asyncio
import logging
from typing import Dict, Any, Callable, Awaitable, Optional
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
import time

logger = logging.getLogger("BackgroundTaskQueue")


class TaskPriority(Enum):
    """작업 우선순위"""
    HIGH = 1      # 즉시 처리 필요 (노트북 업데이트 등)
    NORMAL = 2    # 일반 백그라운드 작업
    LOW = 3       # 지연 가능한 작업


@dataclass
class BackgroundTask:
    """백그라운드 작업 데이터"""
    channel_id: str
    task_name: str
    coroutine: Callable[[], Awaitable[Any]]
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    max_retries: int = 2
    retry_count: int = 0


def _bg_timing(task: "BackgroundTask", start_time: float, run: float, ok: bool) -> None:
    """[2026-10-24 C74 W0-2] 배경 태스크 시간 → turn_timing([BG] 줄 · 정리 끝 셈). 계측이 죽어도 큐는 그대로."""
    try:
        import turn_timing
        turn_timing.bg_done(task.channel_id, task.task_name, max(0.0, start_time - task.created_at), run,
                            retry=task.retry_count, ok=ok)
    except Exception:
        pass


class ChannelTaskQueue:
    """
    채널별 작업 큐 관리자.
    각 채널에 대해 독립적인 FIFO 큐를 유지하여
    파일 쓰기 작업이 순차적으로 이루어지도록 보장합니다.
    """

    def __init__(self):
        # 채널별 작업 큐
        self._queues: Dict[str, asyncio.Queue[BackgroundTask]] = defaultdict(asyncio.Queue)
        # 채널별 워커 태스크
        self._workers: Dict[str, asyncio.Task] = {}
        # 채널별 잠금 (작업 실행 중 동기화)
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # 채널별 마지막 작업 시간 (디버깅/모니터링용)
        self._last_activity: Dict[str, float] = {}
        # 전역 종료 플래그
        self._shutdown = False

    async def enqueue(
        self,
        channel_id: str,
        task_name: str,
        coroutine_factory: Callable[[], Awaitable[Any]],
        priority: TaskPriority = TaskPriority.NORMAL,
        retry_count: int = 0
    ) -> None:
        """
        백그라운드 작업을 큐에 추가합니다.
        [2026-10-24 C74 W4-2·3] retry_count = 이미 쓴 재시도 수(짝 태스크 안에서 죽어 혼자 다시 서는 쪽은 1 — 시도 수가 옛과 같다).

        Args:
            channel_id: 채널 ID
            task_name: 작업 이름 (로깅용)
            coroutine_factory: 실행할 코루틴을 생성하는 팩토리 함수
            priority: 작업 우선순위
        """
        if self._shutdown:
            logger.warning(f"[{channel_id}] Queue is shutting down, task '{task_name}' rejected")
            return

        task = BackgroundTask(
            channel_id=channel_id,
            task_name=task_name,
            coroutine=coroutine_factory,
            priority=priority,
            retry_count=max(0, int(retry_count or 0))
        )

        await self._queues[channel_id].put(task)
        logger.debug(f"[{channel_id}] Enqueued task: {task_name} (priority: {priority.name})")

        # 워커가 없으면 시작
        if channel_id not in self._workers or self._workers[channel_id].done():
            self._workers[channel_id] = asyncio.create_task(
                self._process_queue(channel_id)
            )

    async def _process_queue(self, channel_id: str) -> None:
        """
        특정 채널의 작업 큐를 순차적으로 처리합니다.
        """
        queue = self._queues[channel_id]

        while not self._shutdown:
            try:
                # 타임아웃으로 주기적으로 종료 확인
                try:
                    task = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # 큐가 비어있으면 워커 종료
                    if queue.empty():
                        logger.debug(f"[{channel_id}] Queue empty, worker exiting")
                        break
                    continue

                # 작업 실행 (채널 잠금 내에서)
                async with self._locks[channel_id]:
                    try:
                        await self._execute_task(task)
                    finally:
                        # [2026-10-25 C77 검토] 일 안에서 CancelledError(BaseException)가 새도 셈은 맞춘다 —
                        #   빠지면 queue.join() 이 영영 안 끝나 `!다시` 스냅샷 대기(C-2)가 매 턴 상한까지 서고 거절된다.
                        queue.task_done()
                self._last_activity[channel_id] = time.time()

            except Exception as e:
                logger.error(f"[{channel_id}] Queue processor error: {e}")
                await asyncio.sleep(1.0)  # 에러 시 잠시 대기

    async def _execute_task(self, task: BackgroundTask) -> None:
        """
        단일 작업을 실행합니다 (재시도 로직 포함).
        """
        start_time = time.time()
        try:
            logger.debug(f"[{task.channel_id}] Executing: {task.task_name}")

            # 코루틴 팩토리를 호출하여 실제 코루틴 생성 및 실행
            await task.coroutine()

            elapsed = time.time() - start_time
            logger.debug(f"[{task.channel_id}] Completed: {task.task_name} ({elapsed:.2f}s)")
            # [2026-10-24 C74 W0-2] INFO [BG] 한 줄 — 큐 대기(적재 → 시작) · 실행. 옛 DEBUG 줄은 서버 INFO 로그에 안 보였다.
            _bg_timing(task, start_time, elapsed, ok=True)

        except Exception as e:
            _bg_timing(task, start_time, time.time() - start_time, ok=False)
            task.retry_count += 1

            if task.retry_count <= task.max_retries:
                logger.warning(
                    f"[{task.channel_id}] Task '{task.task_name}' failed, "
                    f"retrying ({task.retry_count}/{task.max_retries}): {e}"
                )
                # 재시도를 위해 큐에 다시 추가
                await asyncio.sleep(0.5 * task.retry_count)  # 지수 백오프
                await self._queues[task.channel_id].put(task)
            else:
                logger.error(
                    f"[{task.channel_id}] Task '{task.task_name}' failed after "
                    f"{task.max_retries} retries: {e}"
                )

    async def wait_for_channel(self, channel_id: str, timeout: float = 30.0) -> bool:
        """
        특정 채널의 모든 대기 중인 작업이 완료될 때까지 대기합니다.

        Args:
            channel_id: 채널 ID
            timeout: 최대 대기 시간 (초)

        Returns:
            True if all tasks completed, False if timeout
        """
        if channel_id not in self._queues:
            return True

        queue = self._queues[channel_id]

        try:
            await asyncio.wait_for(queue.join(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"[{channel_id}] Timeout waiting for queue to drain")
            return False

    async def flush_channel(self, channel_id: str) -> int:
        """채널의 대기 중인 모든 백그라운드 작업을 취소합니다. Returns flushed count."""
        if channel_id not in self._queues:
            return 0
        queue = self._queues[channel_id]
        count = 0
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
                count += 1
            except asyncio.QueueEmpty:
                break
        if count:
            logger.info(f"[{channel_id}] Flushed {count} pending background tasks")
        return count

    def get_queue_size(self, channel_id: str) -> int:
        """특정 채널의 대기 중인 작업 수를 반환합니다."""
        if channel_id not in self._queues:
            return 0
        return self._queues[channel_id].qsize()

    def get_stats(self) -> Dict[str, Any]:
        """전체 큐 시스템 통계를 반환합니다."""
        return {
            "total_channels": len(self._queues),
            "active_workers": sum(1 for w in self._workers.values() if not w.done()),
            "channel_stats": {
                cid: {
                    "queue_size": q.qsize(),
                    "worker_active": cid in self._workers and not self._workers[cid].done(),
                    "last_activity": self._last_activity.get(cid)
                }
                for cid, q in self._queues.items()
            }
        }

    async def shutdown(self, timeout: float = 10.0) -> None:
        """
        큐 시스템을 안전하게 종료합니다.
        대기 중인 모든 작업을 완료하거나 타임아웃될 때까지 대기합니다.
        """
        self._shutdown = True
        logger.info("Background task queue shutting down...")

        # 모든 채널의 작업 완료 대기
        for channel_id in list(self._queues.keys()):
            await self.wait_for_channel(channel_id, timeout=timeout / len(self._queues))

        # 워커 태스크 취소
        for worker in self._workers.values():
            if not worker.done():
                worker.cancel()

        logger.info("Background task queue shutdown complete")


# 전역 싱글톤 인스턴스
_task_queue: Optional[ChannelTaskQueue] = None


def get_task_queue() -> ChannelTaskQueue:
    """전역 작업 큐 인스턴스를 반환합니다."""
    global _task_queue
    if _task_queue is None:
        _task_queue = ChannelTaskQueue()
    return _task_queue


async def enqueue_background_task(
    channel_id: str,
    task_name: str,
    coroutine_factory: Callable[[], Awaitable[Any]],
    priority: TaskPriority = TaskPriority.NORMAL,
    retry_count: int = 0
) -> None:
    """
    백그라운드 작업을 채널 큐에 추가하는 편의 함수입니다.

    사용 예:
        async def my_update_task():
            await domain_manager.save_domain(channel_id, data)

        await enqueue_background_task(
            channel_id,
            "Save Domain",
            my_update_task
        )
    """
    queue = get_task_queue()
    await queue.enqueue(channel_id, task_name, coroutine_factory, priority, retry_count=retry_count)


async def wait_for_channel_tasks(channel_id: str, timeout: float = 30.0) -> bool:
    """채널의 모든 백그라운드 작업 완료를 대기하는 편의 함수입니다."""
    queue = get_task_queue()
    return await queue.wait_for_channel(channel_id, timeout)
