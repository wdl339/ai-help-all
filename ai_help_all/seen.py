"""已读去重：记录已经推送过的论文 id，避免重复打扰。"""
from __future__ import annotations

import json
from pathlib import Path

from .arxiv_crawler import Paper

_DEFAULT_PATH = Path("seen_papers.json")
# 已读 id 文件的最大保留条数，避免无限增长（内部上限，一般无需调整）
_MAX_SEEN_IDS = 20000


def load_seen(path: str | Path = _DEFAULT_PATH) -> set[str]:
    path = Path(path)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("ids", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen(ids: set[str], path: str | Path = _DEFAULT_PATH) -> None:
    path = Path(path)
    # 简单上限，避免文件无限增长
    trimmed = list(ids)[-_MAX_SEEN_IDS:]
    path.write_text(
        json.dumps({"ids": trimmed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def filter_unseen(papers: list[Paper], seen: set[str]) -> list[Paper]:
    return [p for p in papers if p.short_id not in seen]
