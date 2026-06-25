"""今日趋势综述：综观当天「入选论文」，用一次 LLM 调用产出结构化速览。

产物（dict）结构：
    {
      "overview": "今日整体趋势综述（结合研究兴趣）",
      "highlights": [               # 编辑精选 3-5 篇
        {"short_id", "title", "score", "tag", "reason"}
      ],
      "observation": "补充观察（可空）",
      "model": "生成所用模型",
    }
展示在仪表盘 / 邮件 / markdown 顶部；存进 digest JSON 后历史日报亦可复用。
打分/总结失败不影响此步；此步失败也不影响主流程（返回 None）。
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .arxiv_crawler import Paper
from .config import Config
from .llm_client import LLMClient

_TREND_SYS = """你是一位资深科研主编，正在为一位研究者撰写「今日 arXiv 速览」。
研究者的研究兴趣如下：
{interests}

下面是今天已通过相关性筛选的论文（含编号、标题、标签、相关性评分、一句话摘要，部分附作者备注）。
请综观全部论文，为该研究者产出一份{lang}速览，**只返回 JSON**，结构如下：
{{
  "overview": "<不超过200字：今天这批论文整体上的研究趋势/主题/共性，并点出与研究者兴趣的关联>",
  "highlights": [
    {{"index": <论文编号(整数)>, "reason": "<为什么这篇最值得他优先精读，1-2句>"}}
  ],
  "observation": "<可选，不超过60字的补充观察或提醒；没有就写 暂无>"
}}
要求：
- highlights 精选 3-5 篇最值得优先精读的，按重要性排序，index 必须来自上面列表且不重复；
- 忠于材料、不要编造；语言简洁、信息密度高，不要套话与空话。
不要输出 JSON 以外的任何内容。"""


def _one_liner(p: Paper) -> str:
    """取一句话描述：优先用总结里的「一句话总结」，否则退回筛选理由。"""
    s = (p.summary or "").strip()
    if s and not s.startswith("(总结"):
        m = re.search(r"一句话总结[^\n：:]*[：:]\s*(.+)", s)
        if m:
            return m.group(1).split("\n")[0].strip().strip("*").strip()
        return s.split("\n")[0].strip().strip("*").strip()
    return (p.reason or "").strip()


def _build_prompt(papers: list[Paper]) -> str:
    lines = ["# 今日入选论文"]
    for i, p in enumerate(papers):
        entry = (f"\n[{i}] 标题: {p.title}\n标签: {p.tag or '其他'} | 相关性: {p.score}/10"
                 f"\n摘要: {_one_liner(p)}")
        if p.comment:
            entry += f"\n备注: {p.comment[:160]}"
        lines.append(entry)
    return "\n".join(lines)


def _parse_json_obj(text: str) -> dict:
    """从模型输出里稳健地抽出第一个 JSON 对象。失败返回空 dict。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def generate_trend(
    llm: LLMClient,
    cfg: Config,
    papers: list[Paper],
    cancel: Callable[[], bool] | None = None,
) -> dict | None:
    """对当天入选论文生成结构化趋势综述；无论文或失败返回 None。"""
    if not papers:
        return None
    if cancel and cancel():
        return None

    sys = _TREND_SYS.format(interests=cfg.interests or "（未提供，按通用科研视角）",
                            lang=cfg.llm.language)
    out = llm.chat(
        cfg.llm.summarize_model,
        [
            {"role": "system", "content": sys},
            {"role": "user", "content": _build_prompt(papers)},
        ],
        temperature=0.4,
        max_tokens=cfg.llm.trend_max_tokens,
    )
    data = _parse_json_obj(out)
    if not data:
        return None

    overview = str(data.get("overview", "")).strip()
    observation = str(data.get("observation", "")).strip()

    highlights: list[dict] = []
    seen: set[int] = set()
    raw_hl = data.get("highlights")
    if isinstance(raw_hl, list):
        for item in raw_hl:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(papers)) or idx in seen:
                continue
            seen.add(idx)
            p = papers[idx]
            highlights.append({
                "short_id": p.short_id,
                "title": p.title,
                "score": p.score,
                "tag": p.tag or "其他",
                "reason": str(item.get("reason", "")).strip(),
            })

    if not overview and not highlights:
        return None

    return {
        "overview": overview,
        "highlights": highlights,
        "observation": observation,
        "model": cfg.llm.summarize_model,
    }
