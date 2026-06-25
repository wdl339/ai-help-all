"""流水线事件：用回调把运行过程实时抛给调用方（CLI 打印 / 网页 SSE）。"""
from __future__ import annotations

from typing import Any, Callable

# 事件回调：emit(event_type, payload)
Emitter = Callable[[str, dict], None]


def noop_emit(event_type: str, payload: dict) -> None:  # noqa: D401
    """默认空实现。"""
    return None


def fmt_duration(seconds: float) -> str:
    """把秒数格式化成易读时长：如 45.3秒 / 2分30秒 / 1时05分。"""
    s = max(0.0, float(seconds or 0))
    if s < 60:
        return f"{s:.1f} 秒"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m} 分 {sec} 秒"
    h, m = divmod(m, 60)
    return f"{h} 时 {m:02d} 分"


def make_print_emitter() -> Emitter:
    """给 CLI 用：把事件格式化成中文日志行打印。"""

    def emit(event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "day_start":
            print(f"\n===== 处理 {payload.get('date')}（第 {payload.get('index')}/{payload.get('total')} 天）=====")
        elif event_type == "stage":
            name = payload.get("name", "")
            status = payload.get("status", "")
            msg = payload.get("message", "")
            mark = {"start": "▶", "done": "✓"}.get(status, "·")
            print(f"{mark} [{name}] {msg}".rstrip())
        elif event_type == "log":
            print(f"  {payload.get('message', '')}")
        elif event_type == "paper_summarized":
            print(f"    · 已总结  {payload.get('title', '')[:60]}")
        elif event_type == "error":
            print(f"  [错误] {payload.get('message', '')}")
        elif event_type == "usage":
            by_model = payload.get("by_model") or {}
            detail = " ".join(
                f"{m}: {v.get('total_tokens', 0):,}" for m, v in by_model.items()
            )
            print(f"📊 本次用量：{payload.get('requests', 0)} 次请求，"
                  f"{payload.get('total_tokens', 0):,} tokens"
                  + (f"（{detail}）" if detail else ""))
        elif event_type == "done":
            days = payload.get("days")
            extra = f"（{days} 天）" if days and days > 1 else ""
            elapsed = payload.get("elapsed")
            cost = f"，用时 {fmt_duration(elapsed)}" if elapsed is not None else ""
            mark = "⏹ 已停止" if payload.get("stopped") else "完成 ✅"
            print(f"{mark} 共 {payload.get('count', 0)} 篇{extra}{cost}")

    return emit
