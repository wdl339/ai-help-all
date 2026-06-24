"""LLM 客户端：封装 OpenAI 兼容接口，内置每分钟请求数 + token 双限速。

参考 SJTU 交我算 API 文档 https://claw.sjtu.edu.cn/guide/sjtu-api/ 的约束：
- 额度：每分钟 10 次请求、每分钟 100000 token、每周 10 亿 token；
- V3.2(deepseek-chat / deepseek-reasoner) 请求中必须包含 user 角色消息；
- deepseek-reasoner 为深度思考模式，不接受 temperature 等采样参数，
  且返回中带有独立的 reasoning_content 字段（真正答案仍在 content）。
"""
from __future__ import annotations

import threading
import time
from collections import deque

from openai import APITimeoutError, OpenAI

from .config import LLMConfig


def estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数（偏保守地高估，以免触发额度上限）。

    科研论文以英文为主，约 3-4 字符/token；这里用 /3 并加少量开销，
    宁可高估也不要低估，确保不超过每分钟 token 配额。
    """
    if not text:
        return 0
    return len(text) // 3 + 8


class RateLimiter:
    """滑动窗口限速器：同时约束最近 60s 内的请求数与 token 消耗。"""

    def __init__(self, max_requests_per_minute: int, max_tokens_per_minute: int):
        self.max_rpm = max(1, max_requests_per_minute)
        self.max_tpm = max(1, max_tokens_per_minute)
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._token_sum = 0
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        while self._requests and now - self._requests[0] >= 60.0:
            self._requests.popleft()
        while self._tokens and now - self._tokens[0][0] >= 60.0:
            _, tok = self._tokens.popleft()
            self._token_sum -= tok

    def acquire(self, est_tokens: int = 0) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                self._purge(now)

                req_ok = len(self._requests) < self.max_rpm
                # 若单次请求就超过整分钟预算，则无需等待（等也没用），直接放行
                tok_ok = (
                    self._token_sum + est_tokens <= self.max_tpm
                    or not self._tokens
                )
                if req_ok and tok_ok:
                    break

                waits: list[float] = []
                if not req_ok and self._requests:
                    waits.append(60.0 - (now - self._requests[0]) + 0.05)
                if not tok_ok and self._tokens:
                    waits.append(60.0 - (now - self._tokens[0][0]) + 0.05)
                sleep_for = max(waits) if waits else 0.0
                if sleep_for > 0:
                    time.sleep(sleep_for)

            stamp = time.monotonic()
            self._requests.append(stamp)
            self._tokens.append((stamp, est_tokens))
            self._token_sum += est_tokens


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        # max_retries=0：关闭 SDK 自带重试，完全由本类的循环控制超时/重试
        self.client = OpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            timeout=cfg.request_timeout,
            max_retries=0,
        )
        self.limiter = RateLimiter(cfg.requests_per_minute, cfg.tokens_per_minute)

    @staticmethod
    def _is_reasoner(model: str) -> bool:
        return "reasoner" in model.lower()

    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        max_retries: int | None = None,
        **kwargs,
    ) -> str:
        """带双限速、超时与重试的 chat completion，返回文本内容。

        每次请求超时由 client 的 request_timeout 控制；超时或失败会重试，
        总尝试次数为 max_retries（默认取配置 cfg.max_retries），用尽仍失败则抛 RuntimeError。
        """
        retries = max_retries if max_retries is not None else self.cfg.max_retries
        retries = max(1, retries)

        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        est = estimate_tokens(" " * prompt_chars) + max_tokens

        params: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if not self._is_reasoner(model):
            params["temperature"] = temperature
        params.update(kwargs)

        last_err: Exception | None = None
        for attempt in range(retries):
            self.limiter.acquire(est)
            try:
                resp = self.client.chat.completions.create(**params)
                content = resp.choices[0].message.content or ""
                return content.strip()
            except APITimeoutError as e:
                last_err = e
                wait = min(2 ** attempt * 3, 20)
                print(f"  [LLM] 第 {attempt + 1}/{retries} 次超时(>{self.cfg.request_timeout}s)；{wait}s 后重试")
                time.sleep(wait)
            except Exception as e:  # noqa: BLE001 - 其他错误也重试
                last_err = e
                wait = min(2 ** attempt * 5, 30)
                print(f"  [LLM] 第 {attempt + 1}/{retries} 次调用失败: {e}；{wait}s 后重试")
                time.sleep(wait)
        raise RuntimeError(f"LLM 调用多次失败(已尝试 {retries} 次): {last_err}")

    def list_models(self) -> list[str]:
        """列出当前 api-key 可调用的模型 id（也可用于连通性/密钥校验）。"""
        resp = self.client.models.list()
        return [m.id for m in resp.data]
