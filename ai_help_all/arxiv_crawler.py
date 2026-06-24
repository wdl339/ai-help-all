"""arxiv 爬取：按分类拉取最近 N 天的论文。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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


def fetch_recent_papers(cfg: ArxivConfig) -> list[Paper]:
    """拉取指定分类下最近 days_back 天内提交的论文。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.days_back)

    cat_query = " OR ".join(f"cat:{c}" for c in cfg.categories)
    search = arxiv.Search(
        query=cat_query,
        max_results=cfg.max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)

    papers: list[Paper] = []
    seen_ids: set[str] = set()
    for result in client.results(search):
        # 结果按提交时间降序，遇到早于 cutoff 的就可以停了
        if result.published < cutoff:
            break
        p = _to_paper(result)
        if p.short_id in seen_ids:
            continue
        seen_ids.add(p.short_id)
        papers.append(p)

    return papers
