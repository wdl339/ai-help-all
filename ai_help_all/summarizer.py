"""论文总结：对筛选出的论文逐篇生成结构化中文总结。"""
from __future__ import annotations

from .arxiv_crawler import Paper
from .config import Config
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
    )


def summarize_all(llm: LLMClient, cfg: Config, papers: list[Paper]) -> list[Paper]:
    total = len(papers)
    for i, p in enumerate(papers, 1):
        print(f"  [总结] {i}/{total}: {p.title[:60]} ...")
        try:
            p.summary = summarize_paper(llm, cfg, p)
        except RuntimeError as e:
            print(f"  [总结] 失败，跳过: {e}")
            p.summary = "(总结生成失败)"
    return papers
