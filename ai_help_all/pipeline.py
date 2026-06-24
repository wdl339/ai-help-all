"""流水线编排：爬取 -> 去重 -> 并发筛选 -> 并发总结 -> 推送。

通过 emit 回调把每一步的进度实时抛出，CLI 与网页(SSE)共用同一套逻辑。
"""
from __future__ import annotations

from .arxiv_crawler import Paper, fetch_recent_papers
from .config import Config
from .events import Emitter, noop_emit
from .filter import score_papers, select_relevant
from .llm_client import LLMClient
from .push import push_all
from .seen import filter_unseen, load_seen, save_seen
from .summarizer import summarize_all


def run_pipeline(
    cfg: Config,
    emit: Emitter | None = None,
    *,
    dedup: bool = True,
    dry_run: bool = False,
) -> list[Paper]:
    """执行完整流水线，返回最终入选(已总结)的论文列表。"""
    emit = emit or noop_emit

    # 1. 爬取
    emit("stage", {"name": "crawl", "status": "start",
                   "message": f"爬取 arxiv（{', '.join(cfg.arxiv.categories)}，近 {cfg.arxiv.days_back} 天）"})
    papers = fetch_recent_papers(cfg.arxiv)
    emit("stage", {"name": "crawl", "status": "done", "message": f"共抓取 {len(papers)} 篇候选"})
    emit("crawl_done", {"count": len(papers)})
    if not papers:
        emit("done", {"count": 0, "papers": []})
        return []

    # 2. 去重
    seen: set[str] = set()
    if dedup:
        seen = load_seen()
        before = len(papers)
        papers = filter_unseen(papers, seen)
        emit("stage", {"name": "dedup", "status": "done",
                       "message": f"过滤 {before - len(papers)} 篇历史已推送，剩 {len(papers)} 篇"})
    else:
        emit("stage", {"name": "dedup", "status": "done", "message": "跳过去重"})

    if not papers:
        emit("done", {"count": 0, "papers": []})
        return []

    if dry_run:
        emit("stage", {"name": "filter", "status": "done",
                       "message": f"dry-run：仅列出 {len(papers)} 篇候选，不调用 LLM"})
        emit("dry_run_candidates", {"papers": [
            {"short_id": p.short_id, "title": p.title, "categories": p.categories}
            for p in papers
        ]})
        emit("done", {"count": 0, "papers": []})
        return []

    llm = LLMClient(cfg.llm)

    # 3. 并发筛选
    emit("stage", {"name": "filter", "status": "start",
                   "message": f"LLM 相关性打分（阈值 ≥ {cfg.relevance_threshold}，并发 {cfg.llm.max_concurrency}）"})
    score_papers(llm, cfg, papers, emit=emit)
    selected = select_relevant(cfg, papers)
    emit("stage", {"name": "filter", "status": "done", "message": f"命中 {len(selected)} 篇相关论文"})
    emit("selected", {"papers": [p.to_dict() for p in selected]})

    if not selected:
        if dedup:
            save_seen(seen | {p.short_id for p in papers})
        emit("done", {"count": 0, "papers": []})
        return []

    # 4. 并发总结
    emit("stage", {"name": "summarize", "status": "start",
                   "message": f"生成总结（{len(selected)} 篇，并发 {cfg.llm.max_concurrency}）"})
    summarize_all(llm, cfg, selected, emit=emit)
    emit("stage", {"name": "summarize", "status": "done", "message": "总结完成"})

    # 5. 推送
    emit("stage", {"name": "push", "status": "start", "message": "生成日报 / 推送"})
    result = push_all(cfg, selected)
    emit("stage", {"name": "push", "status": "done", "message": f"已生成: {result.get('json')}"})
    emit("pushed", result)

    if dedup:
        save_seen(seen | {p.short_id for p in papers})

    emit("done", {"count": len(selected), "papers": [p.to_dict() for p in selected]})
    return selected
