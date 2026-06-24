"""LLM 客户端：封装 OpenAI 兼容接口，内置每分钟请求数限速。"""
from __future__ import annotations

import threading
import time
from collections import deque

from openai import OpenAI

from .config import LLMConfig


class RateLimiter:
    """简单的滑动窗口限速器：保证最近 60s 内请求数不超过 max_per_minute。"""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max(1, max_per_minute)
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            # 清掉 60s 之前的记录
            while self._calls and now - self._calls[0] >= 60.0:
                self._calls.popleft()
            if len(self._calls) >= self.max_per_minute:
                sleep_for = 60.0 - (now - self._calls[0]) + 0.05
                if sleep_for > 0:
                    time.sleep(sleep_for)
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()
            self._calls.append(time.monotonic())


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
        self.limiter = RateLimiter(cfg.requests_per_minute)

    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.3,
        max_retries: int = 3,
        **kwargs,
    ) -> str:
        """带限速与重试的 chat completion，返回文本内容。"""
        last_err: Exception | None = None
        for attempt in range(max_retries):
            self.limiter.acquire()
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    **kwargs,
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:  # noqa: BLE001 - 统一重试
                last_err = e
                # 退避：触发限流/网络抖动时多等一会
                wait = min(2 ** attempt * 5, 30)
                print(f"  [LLM] 第 {attempt + 1}/{max_retries} 次调用失败: {e}；{wait}s 后重试")
                time.sleep(wait)
        raise RuntimeError(f"LLM 调用多次失败: {last_err}")
