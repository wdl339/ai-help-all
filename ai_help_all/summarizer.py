"""论文总结：并发为每篇论文生成 中文总结 + 摘要翻译 + 作者单位。

作者单位无法从 arxiv API 获得（且 OpenAlex 等对当天新论文有索引延迟），
因此从 PDF 首页文本中由 LLM 抽取。整个过程合并进同一次 LLM 调用以省请求数。
"""
from __future__ import annotations

import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .arxiv_crawler import Paper
from .config import Config
from .events import Emitter, noop_emit
from .llm_client import LLMClient

_SUM_SYS = """你是一个资深科研助理。请基于给定论文信息，用{lang}输出三部分内容，并严格使用下面的分节标记（不要加多余前后缀）：

[总结]
**一句话总结**：<不超过40字>
**研究问题**：<这篇论文要解决什么问题>
**方法/贡献**：<核心方法与主要贡献，2-3点>
**为什么值得看**：<结合摘要给出亮点或潜在价值>

[摘要翻译]
<把英文摘要完整、忠实地翻译成{lang}，不要遗漏>

[作者单位]
<从“论文首页文本”中提取作者所属的机构/单位，每行一个，形如“- 单位名”；若首页文本缺失或无法识别，则只输出一行“- 未知”>"""

# 分节标记
_SEC_RE = re.compile(r"\[(总结|摘要翻译|作者单位)\]")


def _fetch_first_page_text(pdf_url: str, max_chars: int) -> str:
    """下载 PDF 并提取首页文本（用于抽取作者单位）。失败返回空串。"""
    try:
        import fitz  # pymupdf
    except ImportError:
        return ""
    try:
        req = urllib.request.Request(pdf_url, headers={"User-Agent": "ai-help-all/0.1"})
        data = urllib.request.urlopen(req, timeout=30).read()
        doc = fitz.open(stream=data, filetype="pdf")
        text = doc[0].get_text() if doc.page_count else ""
        doc.close()
        return text[:max_chars]
    except Exception:  # noqa: BLE001 - 任何失败都降级为无首页文本
        return ""


def _parse_sections(text: str) -> dict[str, str]:
    """把带 [总结]/[摘要翻译]/[作者单位] 标记的文本拆成各节。"""
    sections: dict[str, str] = {}
    matches = list(_SEC_RE.finditer(text))
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def _parse_affiliations(block: str) -> list[str]:
    affs: list[str] = []
    for line in block.splitlines():
        line = line.strip().lstrip("-•*").strip()
        if not line or line in ("未知", "无", "N/A", "未提供"):
            continue
        if line not in affs:
            affs.append(line)
    return affs


def summarize_paper(llm: LLMClient, cfg: Config, paper: Paper) -> None:
    """生成总结、摘要翻译、作者单位，写回 paper（就地修改）。"""
    first_page = ""
    if cfg.fetch_affiliations:
        first_page = _fetch_first_page_text(paper.pdf_url, cfg.affiliation_pdf_chars)

    sys = _SUM_SYS.format(lang=cfg.llm.language)
    user = (
        f"标题: {paper.title}\n"
        f"分类: {', '.join(paper.categories)}\n"
        f"英文摘要: {paper.abstract}\n\n"
        f"论文首页文本(用于提取作者单位，可能含噪声):\n{first_page or '(无)'}"
    )
    out = llm.chat(
        cfg.llm.summarize_model,
        [
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=cfg.llm.summarize_max_tokens,
    )
    sections = _parse_sections(out)
    # 解析失败则把整段作为总结，至少不丢内容
    paper.summary = sections.get("总结", "").strip() or out.strip()
    paper.abstract_zh = sections.get("摘要翻译", "").strip()
    paper.affiliations = _parse_affiliations(sections.get("作者单位", ""))


def summarize_all(
    llm: LLMClient,
    cfg: Config,
    papers: list[Paper],
    emit: Emitter | None = None,
    cancel: Callable[[], bool] | None = None,
) -> list[Paper]:
    """并发地为每篇论文生成总结/翻译/单位（每篇一个 LLM 请求）。

    cancel(): 返回 True 时停止提交并尽快返回（不等待在途请求）。
    """
    emit = emit or noop_emit
    cancel = cancel or (lambda: False)
    total = len(papers)
    done = 0
    lock = threading.Lock()
    workers = max(1, min(cfg.llm.max_concurrency, total))

    def work(p: Paper) -> Paper:
        if cancel():
            return p
        try:
            summarize_paper(llm, cfg, p)
        except RuntimeError as e:
            emit("error", {"message": f"总结失败: {p.title[:40]}: {e}"})
            p.summary = "(总结生成失败)"
        return p

    ex = ThreadPoolExecutor(max_workers=workers)
    futures = [ex.submit(work, p) for p in papers]
    try:
        for fut in as_completed(futures):
            if cancel():
                ex.shutdown(wait=False, cancel_futures=True)
                break
            p = fut.result()
            with lock:
                done += 1
                emit("summarize_progress", {"done": done, "total": total})
            emit(
                "paper_summarized",
                {
                    "short_id": p.short_id,
                    "title": p.title,
                    "summary": p.summary,
                    "abstract_zh": p.abstract_zh,
                    "affiliations": p.affiliations,
                },
            )
    finally:
        ex.shutdown(wait=False)
    return papers
