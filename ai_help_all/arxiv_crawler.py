"""arxiv 爬取：按"分界小时对齐的自然日"窗口拉取论文。"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from typing import Callable

import arxiv

from .config import ArxivConfig


@dataclass
class Paper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    published: datetime
    updated: datetime
    pdf_url: str
    entry_url: str
    # 后续由筛选/总结阶段填充
    score: int = 0
    reason: str = ""
    summary: str = ""
    abstract_zh: str = ""  # 摘要中文翻译
    affiliations: list[str] = field(default_factory=list)  # 作者单位/发表机构

    @property
    def short_id(self) -> str:
        # 形如 2406.01234v1 -> 2406.01234
        return self.arxiv_id.split("v")[0]

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的 dict（用于 SSE 推送与 JSON 日报）。"""
        return {
            "arxiv_id": self.arxiv_id,
            "short_id": self.short_id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "categories": self.categories,
            "published": self.published.isoformat() if self.published else None,
            "updated": self.updated.isoformat() if self.updated else None,
            "pdf_url": self.pdf_url,
            "entry_url": self.entry_url,
            "score": self.score,
            "reason": self.reason,
            "summary": self.summary,
            "abstract_zh": self.abstract_zh,
            "affiliations": self.affiliations,
        }


def _to_paper(result: arxiv.Result) -> Paper:
    return Paper(
        arxiv_id=result.get_short_id(),
        title=result.title.strip().replace("\n", " "),
        authors=[a.name for a in result.authors],
        abstract=result.summary.strip().replace("\n", " "),
        categories=list(result.categories),
        published=result.published,
        updated=result.updated,
        pdf_url=result.pdf_url,
        entry_url=result.entry_id,
    )


def resolve_window(
    cfg: ArxivConfig, ref_date: str | date_cls | None = None
) -> tuple[datetime, datetime, str]:
    """计算抓取时间窗口 [start, end)（UTC）及其日期标签。

    以本地时区(tz_offset_hours)的"分界小时"(day_boundary_hour)对齐：
    对参考日期 D，窗口结束于 D 当天的分界小时，长度为 days_back 天。
    例如 D=6/24、分界 8 点、days_back=1 → [6/23 08:00, 6/24 08:00)（本地时间）。
    返回 (start_utc, end_utc, date_label)，date_label 即参考日期 YYYY-MM-DD。
    """
    tz = timezone(timedelta(hours=cfg.tz_offset_hours))
    if ref_date is None:
        d = datetime.now(tz).date()
    elif isinstance(ref_date, str):
        d = date_cls.fromisoformat(ref_date)
    else:
        d = ref_date

    end_local = datetime.combine(d, time(hour=cfg.day_boundary_hour), tzinfo=tz)
    start_local = end_local - timedelta(days=max(1, cfg.days_back))
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        d.isoformat(),
    )


def fetch_recent_papers(
    cfg: ArxivConfig,
    ref_date: str | date_cls | None = None,
    notify: Callable[[str], None] | None = None,
) -> list[Paper]:
    """拉取参考日期对应窗口内提交的论文（窗口定义见 resolve_window）。

    遇到 arxiv 限流(HTTP 429)等错误会按指数退避自动重试整次抓取；
    notify(msg) 用于把"限流/重试"提示回传给调用方（网页/CLI）。
    """
    start_utc, end_utc, _ = resolve_window(cfg, ref_date)

    cat_query = " OR ".join(f"cat:{c}" for c in cfg.categories)
    # 用 arxiv 的 submittedDate 范围过滤（UTC），对历史日期也高效准确
    date_filter = f"submittedDate:[{start_utc:%Y%m%d%H%M} TO {end_utc:%Y%m%d%H%M}]"
    query = f"({cat_query}) AND {date_filter}"
    # max_results 是“每天”的上限：窗口跨 days_back 天时，总上限按天数放大
    effective_max = cfg.max_results * max(1, cfg.days_back)

    client = arxiv.Client(
        page_size=cfg.page_size,
        delay_seconds=cfg.request_delay_seconds,
        num_retries=cfg.fetch_retries,
    )

    attempts = max(1, cfg.fetch_retries)
    last_err: Exception | None = None
    for attempt in range(attempts):
        search = arxiv.Search(
            query=query,
            max_results=effective_max,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        try:
            papers: list[Paper] = []
            seen_ids: set[str] = set()
            for result in client.results(search):
                # 安全护栏：只保留窗口内 [start, end) 的论文
                if result.published < start_utc or result.published >= end_utc:
                    continue
                p = _to_paper(result)
                if p.short_id in seen_ids:
                    continue
                seen_ids.add(p.short_id)
                papers.append(p)
            return papers
        except Exception as e:  # noqa: BLE001 - 抓取无副作用，整次重试
            last_err = e
            is_429 = "429" in str(e)
            if attempt == attempts - 1:
                break
            wait = min((30 if is_429 else 10) * (attempt + 1), 120)
            if notify:
                reason = "arxiv 限流(HTTP 429)" if is_429 else f"抓取出错({e})"
                notify(f"{reason}，{wait}s 后重试（第 {attempt + 1}/{attempts} 次）…")
            _time.sleep(wait)

    hint = "arxiv 限流(HTTP 429)，多次重试仍失败，请稍后再试（或减少天数/分类）" \
        if last_err and "429" in str(last_err) else f"arxiv 抓取失败: {last_err}"
    raise RuntimeError(hint)
