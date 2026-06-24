"""流水线事件：用回调把运行过程实时抛给调用方（CLI 打印 / 网页 SSE）。"""
from __future__ import annotations

from typing import Any, Callable

# 事件回调：emit(event_type, payload)
Emitter = Callable[[str, dict], None]


def noop_emit(event_type: str, payload: dict) -> None:  # noqa: D401
    """默认空实现。"""
    return None


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
        elif event_type == "paper_scored":
            p = payload.get("paper", {})
            print(f"    · 打分 {p.get('score')}/10  {p.get('title', '')[:60]}")
        elif event_type == "paper_summarized":
            print(f"    · 已总结  {payload.get('title', '')[:60]}")
        elif event_type == "error":
            print(f"  [错误] {payload.get('message', '')}")
        elif event_type == "done":
            days = payload.get("days")
            extra = f"（{days} 天）" if days and days > 1 else ""
            print(f"完成 ✅ 共 {payload.get('count', 0)} 篇{extra}")

    return emit
