"""相关性筛选：用 LLM 根据用户兴趣给论文批量打分(1-10)。"""
from __future__ import annotations

import json
import re

from .arxiv_crawler import Paper
from .config import Config
from .llm_client import LLMClient

_FILTER_SYS = """你是一个科研论文筛选助手。用户会给出他的研究兴趣，以及一批 arxiv 论文(含编号、标题、摘要)。
请你判断每篇论文与用户兴趣的相关程度，按 1-10 打分(10=高度相关且重要，1=完全不相关)。
只返回 JSON 数组，每个元素形如 {"index": <论文序号>, "score": <1-10整数>, "reason": "<一句中文理由>"}。
不要输出 JSON 以外的任何内容。"""


def _build_user_prompt(interests: str, batch: list[Paper]) -> str:
    lines = [f"# 我的研究兴趣\n{interests}\n", "# 待评估论文"]
    for i, p in enumerate(batch):
        abstract = p.abstract[:1200]
        lines.append(
            f"\n[{i}] 标题: {p.title}\n分类: {', '.join(p.categories)}\n摘要: {abstract}"
        )
    lines.append(
        '\n请输出 JSON 数组，形如: [{"index":0,"score":8,"reason":"..."}]'
    )
    return "\n".join(lines)


def _parse_scores(text: str) -> list[dict]:
    # 去掉可能的 ```json ``` 包裹
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    # 截取第一个 [ 到最后一个 ]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def score_papers(llm: LLMClient, cfg: Config, papers: list[Paper]) -> list[Paper]:
    """对所有论文打分，写回 score/reason，返回原列表(已就地修改)。"""
    batch_size = cfg.llm.filter_batch_size
    total = len(papers)
    for start in range(0, total, batch_size):
        batch = papers[start : start + batch_size]
        prompt = _build_user_prompt(cfg.interests, batch)
        print(f"  [筛选] 打分 {start + 1}-{start + len(batch)} / {total} 篇 ...")
        try:
            out = llm.chat(
                cfg.llm.filter_model,
                [
                    {"role": "system", "content": _FILTER_SYS},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )
        except RuntimeError as e:
            print(f"  [筛选] 该批次失败，跳过: {e}")
            continue

        for item in _parse_scores(out):
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(batch):
                try:
                    batch[idx].score = int(item.get("score", 0))
                except (TypeError, ValueError):
                    batch[idx].score = 0
                batch[idx].reason = str(item.get("reason", "")).strip()
    return papers


def select_relevant(cfg: Config, papers: list[Paper]) -> list[Paper]:
    """按阈值过滤并按分数降序，截断到 max_summarize 篇。"""
    kept = [p for p in papers if p.score >= cfg.relevance_threshold]
    kept.sort(key=lambda p: p.score, reverse=True)
    return kept[: cfg.max_summarize]
