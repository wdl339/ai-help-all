"""Token 用量统计：把每次运行的真实 token 消耗按「自然日」累计到 digests/usage.json。

用途：事后核对额度消耗（SJTU 交我算限每周 10 亿 token、每分钟 10 万 token）。
统计的是「调用发生当天」的用量（按本地日期归档），与论文的参考日期无关。
"""
from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

_PATH = Path("digests/usage.json")
_LOCK = threading.Lock()
_FIELDS = ("requests", "prompt_tokens", "completion_tokens", "total_tokens")


def _empty() -> dict:
    return {f: 0 for f in _FIELDS} | {"by_model": {}}


def _merge(dst: dict, src: dict) -> dict:
    """把 src 的用量累加进 dst（就地修改并返回 dst）。"""
    for k in _FIELDS:
        dst[k] = dst.get(k, 0) + int(src.get(k, 0) or 0)
    bm = dst.setdefault("by_model", {})
    for model, mv in (src.get("by_model") or {}).items():
        d = bm.setdefault(model, {f: 0 for f in _FIELDS})
        for k in _FIELDS:
            d[k] = d.get(k, 0) + int(mv.get(k, 0) or 0)
    return dst


def load_usage(path: str | Path = _PATH) -> dict:
    """读取整个用量账本：{"daily": {"YYYY-MM-DD": {...}}}。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("daily"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"daily": {}}


def record_usage(snapshot: dict, day: str | None = None, path: str | Path = _PATH) -> None:
    """把一次运行的用量快照累加进对应日期（默认今天）。空用量直接忽略。"""
    if not snapshot or not snapshot.get("requests"):
        return
    day = day or date.today().isoformat()
    path = Path(path)
    with _LOCK:
        data = load_usage(path)
        entry = data["daily"].setdefault(day, _empty())
        _merge(entry, snapshot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def usage_summary(days: int = 7, path: str | Path = _PATH) -> dict:
    """汇总今日与最近 N 天（含今日）的用量，供 CLI / 网页展示。"""
    data = load_usage(path)
    daily = data.get("daily", {})
    today = date.today()
    recent = _empty()
    for i in range(max(1, days)):
        d = (today - timedelta(days=i)).isoformat()
        if d in daily:
            _merge(recent, daily[d])
    return {
        "today": daily.get(today.isoformat(), _empty()),
        "recent_days": days,
        "recent": recent,
    }
