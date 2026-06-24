"""相关性筛选：用 LLM 根据用户兴趣给论文批量打分(1-10)。"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .arxiv_crawler import Paper
from .config import Config
from .events import Emitter, noop_emit
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


def _score_one_batch(llm: LLMClient, cfg: Config, batch: list[Paper]) -> int:
    """对单个批次打分，写回 score/reason（就地修改），返回成功解析的条数。"""
    prompt = _build_user_prompt(cfg.interests, batch)
    out = llm.chat(
        cfg.llm.filter_model,
        [
            {"role": "system", "content": _FILTER_SYS},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=cfg.llm.filter_max_tokens,
    )
    parsed = 0
    for item in _parse_scores(out):
        idx = item.get("index")
        if isinstance(idx, int) and 0 <= idx < len(batch):
            try:
                batch[idx].score = int(item.get("score", 0))
            except (TypeError, ValueError):
                batch[idx].score = 0
            batch[idx].reason = str(item.get("reason", "")).strip()
            parsed += 1
    return parsed


def score_papers(
    llm: LLMClient,
    cfg: Config,
    papers: list[Paper],
    emit: Emitter | None = None,
) -> list[Paper]:
    """并发地对所有论文打分，写回 score/reason，返回原列表(已就地修改)。

    各批次相互独立，用线程池并发提交；实际并发度仍受 LLMClient 内置的
    每分钟请求/ token 限速器约束，因此既快又不会超额度。
    """
    emit = emit or noop_emit
    batch_size = cfg.llm.filter_batch_size
    total = len(papers)
    batches = [papers[i : i + batch_size] for i in range(0, total, batch_size)]

    done = 0
    lock = threading.Lock()
    workers = max(1, min(cfg.llm.max_concurrency, len(batches)))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_score_one_batch, llm, cfg, b): b for b in batches}
        for fut in as_completed(futures):
            batch = futures[fut]
            try:
                parsed = fut.result()
                if parsed == 0:
                    emit("error", {"message": "某批次未解析到打分(模型可能返回空，"
                                              "若用思考模型请调大 filter_max_tokens)"})
            except RuntimeError as e:
                emit("error", {"message": f"筛选批次失败，跳过: {e}"})
            with lock:
                done += len(batch)
                emit("score_progress", {"done": done, "total": total})
            emit(
                "scored_batch",
                {
                    "papers": [
                        {
                            "short_id": p.short_id,
                            "title": p.title,
                            "score": p.score,
                            "reason": p.reason,
                            "categories": p.categories,
                        }
                        for p in batch
                    ]
                },
            )
    return papers


def select_relevant(cfg: Config, papers: list[Paper]) -> list[Paper]:
    """按阈值过滤并按分数降序，截断到 max_summarize 篇。"""
    kept = [p for p in papers if p.score >= cfg.relevance_threshold]
    kept.sort(key=lambda p: p.score, reverse=True)
    return kept[: cfg.max_summarize]
