"""流水线编排：按天处理。N 天 = 对每一天分别跑「爬取->去重->筛选->总结->推送」，
各自产出独立日报（digest-YYYY-MM-DD）。CLI 与网页(SSE)共用同一套逻辑。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Callable

from .arxiv_crawler import Paper, fetch_recent_papers, resolve_date, resolve_window
from .config import Config
from .events import Emitter, noop_emit
from .filter import score_papers, select_relevant
from .llm_client import LLMClient
from .push import push_all
from .seen import filter_unseen, load_seen, save_seen
from .summarizer import summarize_all

Cancel = Callable[[], bool]


def _never() -> bool:
    return False


def _run_one_day(
    cfg: Config,
    emit: Emitter,
    llm: LLMClient | None,
    day_label: str,
    *,
    dedup: bool,
    dry_run: bool,
    cancel: Cancel,
) -> tuple[list[Paper], LLMClient | None]:
    """处理单独一天（窗口 = 该天分界点往前 1 天），产出该天的日报。返回 (入选论文, llm)。"""
    start_utc, end_utc, _ = resolve_window(cfg.arxiv, day_label, days=1)

    # 1. 爬取
    emit("stage", {"name": "crawl", "status": "start", "date": day_label,
                   "message": f"爬取 {day_label}（{start_utc:%m-%d %H:%M}~{end_utc:%m-%d %H:%M} UTC）"})
    papers = fetch_recent_papers(
        cfg.arxiv, day_label, days=1, notify=lambda m: emit("log", {"message": m})
    )
    emit("stage", {"name": "crawl", "status": "done", "date": day_label,
                   "message": f"{day_label}：共抓取 {len(papers)} 篇候选"})
    if len(papers) >= cfg.arxiv.max_results:
        emit("error", {"message": f"{day_label} 已达抓取上限 max_results={cfg.arxiv.max_results}/天，"
                                  f"可能有更早的论文未纳入；可调大 max_results。"})
    if not papers:
        emit("stage", {"name": "push", "status": "done", "date": day_label, "message": f"{day_label}：无候选，跳过"})
        return [], llm

    # 2. 去重
    seen: set[str] = set()
    if dedup:
        seen = load_seen()
        before = len(papers)
        papers = filter_unseen(papers, seen)
        emit("stage", {"name": "dedup", "status": "done", "date": day_label,
                       "message": f"{day_label}：过滤 {before - len(papers)} 篇历史已推送，剩 {len(papers)} 篇"})
    else:
        emit("stage", {"name": "dedup", "status": "done", "date": day_label, "message": f"{day_label}：跳过去重"})

    if not papers:
        emit("stage", {"name": "push", "status": "done", "date": day_label, "message": f"{day_label}：去重后无新论文"})
        return [], llm

    if cancel():
        return [], llm

    if dry_run:
        emit("stage", {"name": "filter", "status": "done", "date": day_label,
                       "message": f"{day_label} dry-run：仅列出 {len(papers)} 篇候选，不调用 LLM"})
        emit("dry_run_candidates", {"date": day_label, "papers": [
            {"short_id": p.short_id, "title": p.title, "categories": p.categories, "date": day_label}
            for p in papers
        ]})
        return [], llm

    if llm is None:
        llm = LLMClient(cfg.llm)

    # 3. 并发筛选
    emit("stage", {"name": "filter", "status": "start", "date": day_label,
                   "message": f"{day_label}：LLM 相关性打分（阈值 ≥ {cfg.relevance_threshold}，并发 {cfg.llm.max_concurrency}）"})
    score_papers(llm, cfg, papers, emit=emit, cancel=cancel)
    if cancel():
        return [], llm
    kept = select_relevant(cfg, papers)             # 该天全部过阈值（按分数降序）
    to_summarize = kept[: cfg.max_summarize]          # 该天前 N 篇做 AI 总结
    emit("stage", {"name": "filter", "status": "done", "date": day_label,
                   "message": f"{day_label}：命中 {len(kept)} 篇（{len(to_summarize)} 篇将生成 AI 总结）"})
    sel_payload = []
    for p in kept:
        d = p.to_dict()
        d["will_summarize"] = p.short_id in {q.short_id for q in to_summarize}
        d["date"] = day_label
        sel_payload.append(d)
    emit("selected", {"date": day_label, "papers": sel_payload})

    if not kept:
        if dedup:
            save_seen(seen | {p.short_id for p in papers})
        return [], llm

    # 4. 并发总结
    if to_summarize:
        emit("stage", {"name": "summarize", "status": "start", "date": day_label,
                       "message": f"{day_label}：生成总结（{len(to_summarize)} 篇，并发 {cfg.llm.max_concurrency}）"})
        summarize_all(llm, cfg, to_summarize, emit=emit, cancel=cancel)
        emit("stage", {"name": "summarize", "status": "done", "date": day_label, "message": f"{day_label}：总结完成"})

    # 5. 推送（该天独立日报）
    emit("stage", {"name": "push", "status": "start", "date": day_label, "message": f"{day_label}：生成日报"})
    result = push_all(cfg, kept, date_str=day_label)
    emit("stage", {"name": "push", "status": "done", "date": day_label, "message": f"已生成: {result.get('json')}"})
    emit("pushed", result)

    if dedup:
        save_seen(seen | {p.short_id for p in papers})
    return kept, llm


def run_pipeline(
    cfg: Config,
    emit: Emitter | None = None,
    *,
    dedup: bool = True,
    dry_run: bool = False,
    ref_date: str | None = None,
    cancel: Cancel | None = None,
) -> list[Paper]:
    """对 days_back 天逐天处理，每天产出独立日报。返回所有天入选论文的合并列表。

    cancel(): 协作式取消回调，返回 True 时尽快中止（每天之间、每个任务之间会检查）。
    """
    emit = emit or noop_emit
    cancel = cancel or _never

    base = resolve_date(cfg.arxiv, ref_date)
    days_back = max(1, cfg.arxiv.days_back)
    # 由新到旧依次处理：ref_date, ref_date-1, ...
    day_labels = [(base - timedelta(days=i)).isoformat() for i in range(days_back)]

    llm: LLMClient | None = None
    all_selected: list[Paper] = []
    stopped = False
    for idx, day_label in enumerate(day_labels):
        if cancel():
            stopped = True
            break
        emit("day_start", {"date": day_label, "index": idx + 1, "total": len(day_labels)})
        kept, llm = _run_one_day(cfg, emit, llm, day_label, dedup=dedup, dry_run=dry_run, cancel=cancel)
        all_selected += kept
        if cancel():
            stopped = True
            break

    if stopped or cancel():
        emit("log", {"message": "⏹ 已停止运行（已完成的日报已保存）。"})
        emit("done", {"count": len(all_selected), "days": len(day_labels),
                      "stopped": True, "papers": [p.to_dict() for p in all_selected]})
        return all_selected

    emit("done", {"count": len(all_selected), "days": len(day_labels),
                  "papers": [p.to_dict() for p in all_selected]})
    return all_selected
