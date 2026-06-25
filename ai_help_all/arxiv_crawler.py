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
    # arxiv API 直接提供的元信息（多数新论文里 journal_ref/doi 为空）
    comment: str = ""          # 作者备注：常含会议/期刊接收信息、代码链接、页数等
    primary_category: str = ""  # 主分类（单个，比 categories 列表更聚焦）
    journal_ref: str = ""      # 期刊/会议引用（已正式发表才有）
    doi: str = ""              # DOI（已正式发表才有）
    # 后续由筛选/总结阶段填充
    score: int = 0
    reason: str = ""
    tag: str = ""          # 分类标签（来自配置的 tags）
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
            "primary_category": self.primary_category,
            "comment": self.comment,
            "journal_ref": self.journal_ref,
            "doi": self.doi,
            "published": self.published.isoformat() if self.published else None,
            "updated": self.updated.isoformat() if self.updated else None,
            "pdf_url": self.pdf_url,
            "entry_url": self.entry_url,
            "score": self.score,
            "reason": self.reason,
            "tag": self.tag,
            "summary": self.summary,
            "abstract_zh": self.abstract_zh,
            "affiliations": self.affiliations,
        }


def _clean(text: str | None) -> str:
    """把可能含换行/多余空白的字段压成单行（comment/journal_ref 等）。"""
    return " ".join((text or "").split())


def _api_affiliations(result: arxiv.Result) -> list[str]:
    """收集 arxiv API 自带的作者单位（<arxiv:affiliation>，去重保序）。

    多数新论文作者未填该字段，此时返回空列表，总结阶段再回退到从 PDF 首页抽取。
    """
    affs: list[str] = []
    for a in result.authors:
        for raw in getattr(a, "affiliation", None) or []:
            name = _clean(raw)
            if name and name not in affs:
                affs.append(name)
    return affs


def _to_paper(result: arxiv.Result) -> Paper:
    return Paper(
        arxiv_id=result.get_short_id(),
        title=result.title.strip().replace("\n", " "),
        authors=[a.name for a in result.authors],
        abstract=result.summary.strip().replace("\n", " "),
        categories=list(result.categories),
        primary_category=result.primary_category or "",
        comment=_clean(result.comment),
        journal_ref=_clean(result.journal_ref),
        doi=_clean(result.doi),
        published=result.published,
        updated=result.updated,
        pdf_url=result.pdf_url,
        entry_url=result.entry_id,
        affiliations=_api_affiliations(result),
    )


def resolve_date(cfg: ArxivConfig, ref_date: str | date_cls | None = None) -> date_cls:
    """把参考日期解析为本地时区的 date（默认今天）。"""
    tz = timezone(timedelta(hours=cfg.tz_offset_hours))
    if ref_date is None:
        return datetime.now(tz).date()
    if isinstance(ref_date, str):
        return date_cls.fromisoformat(ref_date)
    return ref_date


def resolve_window(
    cfg: ArxivConfig, ref_date: str | date_cls | None = None, days: int | None = None
) -> tuple[datetime, datetime, str]:
    """计算抓取时间窗口 [start, end)（UTC）及其日期标签。

    以本地时区(tz_offset_hours)的"分界小时"(day_boundary_hour)对齐：
    对参考日期 D，窗口结束于 D 当天的分界小时，长度为 days 天（默认 cfg.days_back）。
    例如 D=6/24、分界 8 点、days=1 → [6/23 08:00, 6/24 08:00)（本地时间）。
    返回 (start_utc, end_utc, date_label)，date_label 即参考日期 YYYY-MM-DD。
    """
    d = resolve_date(cfg, ref_date)
    span = max(1, days if days is not None else cfg.days_back)
    end_local = datetime.combine(d, time(hour=cfg.day_boundary_hour), tzinfo=tz_of(cfg))
    start_local = end_local - timedelta(days=span)
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        d.isoformat(),
    )


def tz_of(cfg: ArxivConfig) -> timezone:
    return timezone(timedelta(hours=cfg.tz_offset_hours))


def fetch_recent_papers(
    cfg: ArxivConfig,
    ref_date: str | date_cls | None = None,
    days: int | None = None,
    notify: Callable[[str], None] | None = None,
) -> list[Paper]:
    """拉取参考日期对应窗口内提交的论文（窗口定义见 resolve_window）。

    遇到 arxiv 限流(HTTP 429)等错误会按指数退避自动重试整次抓取；
    notify(msg) 用于把"限流/重试"提示回传给调用方（网页/CLI）。
    """
    span = max(1, days if days is not None else cfg.days_back)
    start_utc, end_utc, _ = resolve_window(cfg, ref_date, span)

    cat_query = " OR ".join(f"cat:{c}" for c in cfg.categories)
    # 用 arxiv 的 submittedDate 范围过滤（UTC），对历史日期也高效准确
    date_filter = f"submittedDate:[{start_utc:%Y%m%d%H%M} TO {end_utc:%Y%m%d%H%M}]"
    query = f"({cat_query}) AND {date_filter}"
    # max_results 是“每天”的上限：窗口跨 span 天时，总上限按天数放大
    effective_max = cfg.max_results * span

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
