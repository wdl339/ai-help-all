"""论文总结：并发为每篇论文生成 中文总结 + 摘要翻译 + 作者单位。

总结依据：默认基于**论文全文**（先下 PDF 抽文本，失败再抓 arXiv HTML 版，
都不行才回退摘要），全文长度由 cfg.fulltext_max_chars 截断。可用 cfg.summarize_fulltext
关闭、只用摘要。注意：相关性「打分」始终只用摘要，全文仅用于本阶段。

作者单位无法从 arxiv API 获得，故由 LLM 从论文首页/正文开头文本中抽取，
与总结合并进同一次 LLM 调用以省请求数。
"""
from __future__ import annotations

import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Callable

from .arxiv_crawler import Paper
from .config import Config
from .events import Emitter, noop_emit
from .llm_client import LLMClient

# 提取到的正文低于此字符数即视为失败，转下一个来源
_MIN_TEXT_CHARS = 800
_HTTP_HEADERS = {"User-Agent": "ai-help-all/0.1 (arxiv daily digest)"}

_SUM_SYS = """你是一个资深科研助理，目标是把论文讲到「有 CS 和 AI 基础但非本子领域的人也能读懂」。
请基于给定论文信息（通常含全文，可能已截断），用{lang}输出三部分内容，并严格使用下面的分节标记（不要加多余前后缀）。

写作要求：
- 语言通俗清晰，不要泛泛而谈、不要堆砌术语；
- 遇到晦涩的概念/专有名词，先用一两句大白话解释它是什么、再展开；但是，也不要过度解释，一些比较常见的概念也不需要解释；
- 内容要忠于论文，不要编造数字或结论；不确定的地方如实说明。

[总结]
**一句话总结**：<点明这篇论文做了什么、达到什么效果，不超过80字>
**研究背景与问题**：<它针对什么问题、为什么这个问题重要；交代必要背景；其中出现的关键或晦涩概念要用通俗语言解释清楚>
**核心洞见**：<这篇工作最关键的想法 / insight 是什么，为什么这么做能奏效（点出关键直觉，而不仅是罗列步骤）>
**方法与贡献**：<核心方法的关键步骤，以及主要贡献，分点写清、具体而不空泛>
**相关研究**：<与本文最相关的研究方向或代表性工作，以及本文相比它们的区别/改进；论文未提及时可略写>
**可进一步探索的点**：<本文的局限、尚未解决的问题，以及值得后续探索的方向>

[摘要翻译]
<把英文摘要完整、忠实地翻译成{lang}，不要遗漏>

[作者单位]
<从“论文首页文本”中提取作者所属的机构/单位，每行一个，形如“- 单位名”；若首页文本缺失或无法识别，则只输出一行“- 未知”>"""

# 分节标记
_SEC_RE = re.compile(r"\[(总结|摘要翻译|作者单位)\]")


def _http_get(url: str, timeout: int = 30) -> bytes | None:
    """GET 原始字节，失败返回 None。"""
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        return urllib.request.urlopen(req, timeout=timeout).read()
    except Exception:  # noqa: BLE001 - 任何网络错误都降级
        return None


def _pdf_extract(data: bytes) -> tuple[str, str]:
    """从 PDF 字节中提取 (全文, 首页文本)；失败返回 ("", "")。"""
    try:
        import fitz  # pymupdf
    except ImportError:
        return "", ""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        pages = [page.get_text() for page in doc]
        doc.close()
    except Exception:  # noqa: BLE001
        return "", ""
    full = "\n".join(pages).strip()
    first = pages[0].strip() if pages else ""
    return full, first


class _HTMLTextExtractor(HTMLParser):
    """极简 HTML→纯文本：丢弃 script/style，逐段收集可见文本。"""

    _SKIP = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            s = data.strip()
            if s:
                self._parts.append(s)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._parts)).strip()


def _html_extract(data: bytes) -> str:
    """把 HTML 字节解析成纯文本；失败返回空串。"""
    try:
        parser = _HTMLTextExtractor()
        parser.feed(data.decode("utf-8", errors="ignore"))
        return parser.get_text()
    except Exception:  # noqa: BLE001
        return ""


def _fetch_full_text(paper: Paper, max_chars: int) -> tuple[str, str, str]:
    """获取论文正文用于总结。

    依次尝试：PDF（pymupdf 抽文本）→ arXiv HTML 版（解析纯文本）→ 失败。
    返回 (正文, 首页/开头文本, 来源)；来源 ∈ {"pdf", "html", ""}。
    正文截断到 max_chars；首页文本用于抽作者单位。
    """
    # 1) PDF
    data = _http_get(paper.pdf_url)
    if data:
        full, first = _pdf_extract(data)
        if len(full) >= _MIN_TEXT_CHARS:
            return full[:max_chars], first, "pdf"

    # 2) arXiv HTML 版（带版本号优先，回退到无版本号的 id）
    for hid in (paper.arxiv_id, paper.short_id):
        if not hid:
            continue
        data = _http_get(f"https://arxiv.org/html/{hid}")
        if data:
            text = _html_extract(data)
            if len(text) >= _MIN_TEXT_CHARS:
                # HTML 无明确「首页」，用开头一段近似作者/单位区
                return text[:max_chars], text[:2000], "html"

    # 3) 都失败
    return "", "", ""


def _fetch_first_page_text(pdf_url: str, max_chars: int) -> str:
    """只下载 PDF 首页文本（用于关闭全文总结时仍能抽作者单位）。失败返回空串。"""
    data = _http_get(pdf_url)
    if not data:
        return ""
    _, first = _pdf_extract(data)
    return first[:max_chars]


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
    """生成总结、摘要翻译、作者单位，写回 paper（就地修改）。

    总结依据：cfg.summarize_fulltext 为真时优先用全文（PDF→HTML→回退摘要）；
    全文已含首页，作者单位即从其开头抽取，无需再单独下载。关闭全文时退化为
    「仅摘要总结 + 单独下首页抽单位」的旧行为。
    """
    body, first_page = "", ""
    if cfg.summarize_fulltext:
        body, first_page, _ = _fetch_full_text(paper, cfg.fulltext_max_chars)
    if cfg.fetch_affiliations and not first_page:
        # 未取到正文（或关闭全文）时，单独取首页用于抽作者单位
        first_page = _fetch_first_page_text(paper.pdf_url, cfg.affiliation_pdf_chars)

    sys = _SUM_SYS.format(lang=cfg.llm.language)
    parts = [
        f"标题: {paper.title}",
        f"分类: {', '.join(paper.categories)}",
        f"英文摘要: {paper.abstract}",
    ]
    if body:
        parts.append(f"\n论文正文(用于总结，可能含解析噪声/已截断):\n{body}")
    else:
        parts.append("\n（未取到正文，请仅依据上面的英文摘要进行总结）")
    if cfg.fetch_affiliations:
        parts.append(f"\n论文首页文本(用于提取作者单位，可能含噪声):\n{first_page or '(无)'}")
    user = "\n".join(parts)

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
