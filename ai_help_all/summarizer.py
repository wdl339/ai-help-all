"""论文总结：对筛选出的论文并发生成结构化中文总结。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .arxiv_crawler import Paper
from .config import Config
from .events import Emitter, noop_emit
from .llm_client import LLMClient

_SUM_SYS = """你是一个资深科研助理。请基于给定论文的标题和摘要，用{lang}写一段精炼总结，帮助研究者快速判断是否值得精读。
严格按如下格式输出(不要加多余前后缀)：
**一句话总结**：<不超过40字>
**研究问题**：<这篇论文要解决什么问题>
**方法/贡献**：<核心方法与主要贡献，2-3点>
**为什么值得看**：<结合摘要给出亮点或潜在价值>"""


def summarize_paper(llm: LLMClient, cfg: Config, paper: Paper) -> str:
    sys = _SUM_SYS.format(lang=cfg.llm.language)
    user = (
        f"标题: {paper.title}\n"
        f"作者: {', '.join(paper.authors[:8])}\n"
        f"分类: {', '.join(paper.categories)}\n"
        f"摘要: {paper.abstract}"
    )
    return llm.chat(
        cfg.llm.summarize_model,
        [
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=cfg.llm.summarize_max_tokens,
    )


def summarize_all(
    llm: LLMClient,
    cfg: Config,
    papers: list[Paper],
    emit: Emitter | None = None,
) -> list[Paper]:
    """并发地为每篇论文生成总结（每篇一个请求，互相独立）。"""
    emit = emit or noop_emit
    total = len(papers)
    done = 0
    lock = threading.Lock()
    workers = max(1, min(cfg.llm.max_concurrency, total))

    def work(p: Paper) -> Paper:
        try:
            p.summary = summarize_paper(llm, cfg, p)
        except RuntimeError as e:
            emit("error", {"message": f"总结失败: {p.title[:40]}: {e}"})
            p.summary = "(总结生成失败)"
        return p

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(work, p) for p in papers]
        for fut in as_completed(futures):
            p = fut.result()
            with lock:
                done += 1
                emit("summarize_progress", {"done": done, "total": total})
            emit(
                "paper_summarized",
                {"short_id": p.short_id, "title": p.title, "summary": p.summary},
            )
    return papers
