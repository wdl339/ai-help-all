"""流水线编排：爬取 -> 去重 -> 并发筛选 -> 并发总结 -> 推送。

通过 emit 回调把每一步的进度实时抛出，CLI 与网页(SSE)共用同一套逻辑。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta, timezone

from .arxiv_crawler import Paper, fetch_recent_papers, resolve_window
from .config import Config
from .events import Emitter, noop_emit
from .filter import score_papers, select_relevant
from .llm_client import LLMClient
from .push import push_all
from .seen import filter_unseen, load_seen, save_seen
from .summarizer import summarize_all


def _pick_to_summarize(cfg: Config, kept: list[Paper]) -> set[str]:
    """在窗口内按"自然日"(分界小时对齐)分组，每天取相关性最高的前 max_summarize 篇。

    返回需要做 AI 总结的论文 short_id 集合。kept 需已按分数降序。
    """
    tz = timezone(timedelta(hours=cfg.arxiv.tz_offset_hours))
    boundary = cfg.arxiv.day_boundary_hour

    def day_label(p: Paper) -> str:
        local = p.published.astimezone(tz)
        d = local.date()
        # 当天分界小时之后算作"次日"那一天（与抓取窗口的日定义一致）
        if local.hour >= boundary:
            d = d + timedelta(days=1)
        return d.isoformat()

    by_day: dict[str, list[Paper]] = defaultdict(list)
    for p in kept:  # kept 已按分数降序
        by_day[day_label(p)].append(p)

    to_sum: set[str] = set()
    for plist in by_day.values():
        for p in plist[: cfg.max_summarize]:
            to_sum.add(p.short_id)
    return to_sum


def run_pipeline(
    cfg: Config,
    emit: Emitter | None = None,
    *,
    dedup: bool = True,
    dry_run: bool = False,
    ref_date: str | None = None,
) -> list[Paper]:
    """执行完整流水线，返回最终入选(已总结)的论文列表。

    ref_date: 参考日期 YYYY-MM-DD（默认今天，按本地时区）；窗口定义见 arxiv_crawler。
    """
    emit = emit or noop_emit

    start_utc, end_utc, date_label = resolve_window(cfg.arxiv, ref_date)

    # 1. 爬取
    emit("stage", {"name": "crawl", "status": "start",
                   "message": f"爬取 arxiv（{', '.join(cfg.arxiv.categories)}；{date_label} 窗口，"
                              f"{start_utc:%m-%d %H:%M}~{end_utc:%m-%d %H:%M} UTC）"})
    papers = fetch_recent_papers(
        cfg.arxiv, ref_date, notify=lambda m: emit("log", {"message": m})
    )
    emit("stage", {"name": "crawl", "status": "done", "message": f"共抓取 {len(papers)} 篇候选"})
    effective_max = cfg.arxiv.max_results * max(1, cfg.arxiv.days_back)
    if len(papers) >= effective_max:
        emit("error", {"message": f"已达抓取上限（max_results={cfg.arxiv.max_results}/天 × {cfg.arxiv.days_back} 天"
                                  f"={effective_max}），可能有更早的论文未纳入；可调大 max_results。"})
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
    kept = select_relevant(cfg, papers)            # 全部过阈值（按分数降序）
    # 每天（按分界小时对齐）取相关性最高的前 max_summarize 篇做 AI 总结
    to_sum_ids = _pick_to_summarize(cfg, kept)
    to_summarize = [p for p in kept if p.short_id in to_sum_ids]
    emit("stage", {"name": "filter", "status": "done",
                   "message": f"命中 {len(kept)} 篇（其中 {len(to_summarize)} 篇将生成 AI 总结，"
                              f"每天最多 {cfg.max_summarize} 篇）"})
    # selected 事件带 will_summarize 标记：被选中的会总结，其余仅保留非 AI 信息
    sel_payload = []
    for p in kept:
        d = p.to_dict()
        d["will_summarize"] = p.short_id in to_sum_ids
        sel_payload.append(d)
    emit("selected", {"papers": sel_payload})

    if not kept:
        if dedup:
            save_seen(seen | {p.short_id for p in papers})
        emit("done", {"count": 0, "papers": []})
        return []

    # 4. 并发总结（只总结前 N 篇）
    if to_summarize:
        emit("stage", {"name": "summarize", "status": "start",
                       "message": f"生成总结（{len(to_summarize)} 篇，并发 {cfg.llm.max_concurrency}）"})
        summarize_all(llm, cfg, to_summarize, emit=emit)
        emit("stage", {"name": "summarize", "status": "done", "message": "总结完成"})

    # 5. 推送（保留全部过阈值论文；未总结的也写入，可在网页上按需生成）
    emit("stage", {"name": "push", "status": "start", "message": "生成日报 / 推送"})
    result = push_all(cfg, kept, date_str=date_label)
    emit("stage", {"name": "push", "status": "done", "message": f"已生成: {result.get('json')}"})
    emit("pushed", result)

    if dedup:
        save_seen(seen | {p.short_id for p in papers})

    emit("done", {"count": len(kept), "summarized": len(to_summarize),
                  "papers": [p.to_dict() for p in kept]})
    return kept
