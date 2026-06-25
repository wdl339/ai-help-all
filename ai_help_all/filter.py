"""相关性筛选：用 LLM 根据用户兴趣给论文批量打分(1-10)。"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .arxiv_crawler import Paper
from .config import Config
from .events import Emitter, noop_emit
from .llm_client import LLMClient

_FILTER_SYS = """你是一个严格的科研论文筛选助手，为一位有明确研究方向的研究者把关。用户会给出他的研究兴趣（其中可能包含「关注方向」「排除/不感兴趣的类型」等说明）、一个可用标签列表，以及一批 arxiv 论文(含编号、标题、摘要、分类，部分附作者备注)。

请按 1-10 给每篇打分。总原则：证据不足或模棱两可时，宁可给低分，不要给高分。分档参考：
- 1-3：与研究兴趣无关；
- 4-6：仅部分相关或处于边界；
- 7-8：命中研究兴趣；
- 9-10：高度相关且重要。

判分要点：
- 相关性以「是否命中研究兴趣所述的核心贡献」为准，而不是表面是否出现某些热词/术语；
- 严格执行用户在研究兴趣中写明的「排除/不感兴趣」类型：命中者压到低分或边界，除非它另有可迁移、可复用的方法或系统贡献；
- 不要因为论文表面蹭了热门方向就给高分；本身很弱、或主要是纯应用/纯硬件/纯算法/仅 benchmark 的论文应给低分；
- 作者备注(comment)中的顶会/顶刊接收是「重要性」的正向信号，可据此适度上调，但不替代相关性，也不要仅因被接收就给高分。

并从"可用标签"中为每篇选一个最贴切的标签(tag)，无法明确归类时用 "其他"。
只返回 JSON 数组，每个元素形如 {"index": <论文序号>, "score": <1-10整数>, "reason": "<一句中文理由>", "tag": "<标签>"}。
不要输出 JSON 以外的任何内容。"""


def _build_user_prompt(interests: str, batch: list[Paper], abstract_chars: int,
                       tags: list[str]) -> str:
    lines = [
        f"# 我的研究兴趣\n{interests}\n",
        f"# 可用标签（tag 必须从中选一个）\n{', '.join(tags)}\n",
        "# 待评估论文",
    ]
    for i, p in enumerate(batch):
        abstract = p.abstract[:abstract_chars]
        entry = f"\n[{i}] 标题: {p.title}\n分类: {', '.join(p.categories)}\n摘要: {abstract}"
        if p.comment:
            # 备注可能很长（含 latex/链接），截断以控制 token
            entry += f"\n作者备注: {p.comment[:300]}"
        lines.append(entry)
    lines.append(
        '\n请输出 JSON 数组，形如: [{"index":0,"score":8,"reason":"...","tag":"训练"}]'
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
    """对单个批次打分+打标签（就地修改），返回成功解析的条数。"""
    prompt = _build_user_prompt(cfg.interests, batch, cfg.llm.filter_abstract_chars, cfg.tags)
    out = llm.chat(
        cfg.llm.filter_model,
        [
            {"role": "system", "content": _FILTER_SYS},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=cfg.llm.filter_max_tokens,
    )
    tagset = set(cfg.tags)
    parsed = 0
    for item in _parse_scores(out):
        idx = item.get("index")
        if isinstance(idx, int) and 0 <= idx < len(batch):
            try:
                batch[idx].score = int(item.get("score", 0))
            except (TypeError, ValueError):
                batch[idx].score = 0
            batch[idx].reason = str(item.get("reason", "")).strip()
            tag = str(item.get("tag", "")).strip()
            batch[idx].tag = tag if tag in tagset else "其他"
            parsed += 1
    return parsed


def score_papers(
    llm: LLMClient,
    cfg: Config,
    papers: list[Paper],
    emit: Emitter | None = None,
    cancel: Callable[[], bool] | None = None,
) -> list[Paper]:
    """并发地对所有论文打分，写回 score/reason，返回原列表(已就地修改)。

    各批次相互独立，用线程池并发提交；实际并发度仍受 LLMClient 内置的
    每分钟请求/ token 限速器约束，因此既快又不会超额度。
    cancel(): 返回 True 时停止提交并尽快返回（不等待在途请求）。
    """
    emit = emit or noop_emit
    cancel = cancel or (lambda: False)
    batch_size = cfg.llm.filter_batch_size
    total = len(papers)
    batches = [papers[i : i + batch_size] for i in range(0, total, batch_size)]

    done = 0
    lock = threading.Lock()
    workers = max(1, min(cfg.llm.max_concurrency, len(batches)))

    def work(b: list[Paper]) -> int:
        if cancel():
            return 0
        return _score_one_batch(llm, cfg, b)

    ex = ThreadPoolExecutor(max_workers=workers)
    futures = {ex.submit(work, b): b for b in batches}
    try:
        for fut in as_completed(futures):
            if cancel():
                ex.shutdown(wait=False, cancel_futures=True)
                break
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
    finally:
        ex.shutdown(wait=False)
    return papers


def select_relevant(cfg: Config, papers: list[Paper]) -> list[Paper]:
    """返回所有达到阈值的论文，按分数降序（不截断；是否总结由调用方决定）。"""
    kept = [p for p in papers if p.score >= cfg.relevance_threshold]
    kept.sort(key=lambda p: p.score, reverse=True)
    return kept
