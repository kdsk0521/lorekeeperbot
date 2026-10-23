# -*- coding: utf-8 -*-
"""채널별 턴 잠금 — [2026-10-23 C60 M2c] 공용 자리.

옛 자리는 `main.channel_locks`(main.py 지역)였다. Activity 편집(activity_edit)이 **같은 잠금**을 쥐어야
턴·명령과 겹쳐 쓰지 않는데, activity_server 가 main 을 import 하면 안 된다(스크립트로 돌면 `__main__` 이라
모듈이 두 번 실행된다). 그래서 표만 여기로 옮기고 main 은 이걸 쓴다. 잠금을 잡는 규칙은 그대로다:
  · main._process_message — 메시지 하나(턴·명령·OOC)를 통째로 잡는다
  · activity_edit.act — 편집 한 건을 잡는다(턴이 도는 중이면 기다리다 LOCK_WAIT 넘으면 409)
"""
import asyncio
from collections import defaultdict
from typing import Dict

channel_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
