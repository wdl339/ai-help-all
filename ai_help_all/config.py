"""配置加载：从 config.yaml 读取，密钥优先从环境变量取。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv 是可选依赖
    pass


@dataclass
class ArxivConfig:
    categories: list[str] = field(default_factory=lambda: ["cs.AI"])
    days_back: int = 1
    # 每天的抓取上限；多天窗口时实际上限 = max_results × days_back
    max_results: int = 200
    # “一天”的分界小时（本地时区）。例如 8 表示一天从早上 8 点算到次日 8 点。
    day_boundary_hour: int = 8
    # 本地时区相对 UTC 的偏移小时数（北京 = 8），用于把上面的“8点”对齐到 UTC。
    tz_offset_hours: int = 8
    # arxiv 抓取调优：每页条数 / 请求间隔(秒，礼貌延迟) / 失败重试次数
    page_size: int = 100
    request_delay_seconds: float = 3.0
    fetch_retries: int = 3


@dataclass
class LLMConfig:
    base_url: str = "https://models.sjtu.edu.cn/api/v1"
    api_key: str = ""
    # 筛选模型：minimax-m2.7 做批量结构化打分（返回 JSON 数组）；思考模型，filter_max_tokens 需留足
    filter_model: str = "minimax-m2.7"
    # 总结模型：minimax-m2.7 总结质量高（注意它是思考模型，需较大 max_tokens）
    summarize_model: str = "minimax-m2.7"
    # 速率限制（申请额度：每分钟 10 次请求，留 1 余量）
    requests_per_minute: int = 9
    # token 限速（申请额度：每分钟 100000 token，默认留约 10% 余量）
    tokens_per_minute: int = 90000
    # 并发线程数（同时在途的请求数；受上面的限速器约束，调大可隐藏网络延迟）
    max_concurrency: int = 8
    # 单次请求超时(秒)，超时即按下面的次数重试
    request_timeout: int = 180
    # 超时/失败的最大尝试次数，超过则放弃（该篇标记为失败，可在网页上单独重试）
    max_retries: int = 2
    # 一次筛选 prompt 里塞多少篇论文
    filter_batch_size: int = 20
    # 筛选时每篇摘要截断到多少字符（控制单次请求 token；够覆盖绝大多数摘要）
    filter_abstract_chars: int = 1200
    # 单次请求的最大输出 token（思考模型会先消耗 token 做推理，需留足余量）
    filter_max_tokens: int = 8192
    # 总结要产出 多段深度解读 + 摘要翻译 + 作者单位（思考模型还会先消耗 token 推理），故留足余量
    summarize_max_tokens: int = 8192
    # 趋势综述的最大输出 token（思考模型需留足推理余量）
    trend_max_tokens: int = 6144
    language: str = "中文"


@dataclass
class EmailConfig:
    # 注意：是否发邮件已改为“运行时”控制（CLI --email / 网页勾选「发送邮件」），默认关闭，
    # 不再放在 config 里。这里只保留 SMTP 连接参数。
    smtp_host: str = ""
    smtp_port: int = 465
    use_ssl: bool = True
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)


@dataclass
class PushConfig:
    markdown: bool = True
    email: EmailConfig = field(default_factory=EmailConfig)


@dataclass
class Config:
    arxiv: ArxivConfig = field(default_factory=ArxivConfig)
    interests: str = ""
    relevance_threshold: int = 6
    # 每份日报（每天一份）最多自动 AI 总结多少篇；多天会逐天各出一份日报
    max_summarize: int = 15
    # 给入选论文打的标签集合（在 config.yaml 的 tags 里定义）；"其他" 会自动确保存在
    tags: list[str] = field(default_factory=lambda: ["其他"])
    # 是否下载 PDF 首页提取作者单位/发表机构（关掉可省下载时间）
    fetch_affiliations: bool = True
    # 提取作者单位时，PDF 首页文本截断到多少字符（首页含作者/单位即可）
    affiliation_pdf_chars: int = 1800
    # 总结/筛选/推送展示作者时最多列几位
    max_authors_shown: int = 6
    # 总结是否基于论文全文：先下 PDF 抽文本，失败抓 arXiv HTML 版，最后回退摘要。关掉则只用摘要。
    # 注意：打分(筛选)始终只用摘要，全文仅用于「总结」阶段。
    summarize_fulltext: bool = True
    # 全文喂给 LLM 前截断到多少字符（越大越完整但越耗 token）
    fulltext_max_chars: int = 100000
    # 是否在每份日报顶部生成「今日趋势综述」（综观当天入选论文，额外 1 次 LLM 调用/天）
    trend_summary: bool = True
    llm: LLMConfig = field(default_factory=LLMConfig)
    push: PushConfig = field(default_factory=PushConfig)


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"找不到配置文件 {path}，请先 `cp config.example.yaml config.yaml` 并修改。"
        )

    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    arxiv = ArxivConfig(**(raw.get("arxiv") or {}))

    llm_raw = raw.get("llm") or {}
    llm = LLMConfig(**llm_raw)
    # 密钥环境变量优先
    llm.api_key = os.getenv("AI_HELP_ALL_API_KEY") or llm.api_key
    if not llm.api_key:
        raise ValueError(
            "未配置 LLM api_key：请设置环境变量 AI_HELP_ALL_API_KEY 或在 config.yaml 的 llm.api_key 填入。"
        )

    push_raw = raw.get("push") or {}
    email_raw = dict(push_raw.get("email") or {})
    # 兼容旧配置：enabled 已改为运行时控制，若残留则忽略
    email_raw.pop("enabled", None)
    email = EmailConfig(**email_raw)
    email.password = os.getenv("AI_HELP_ALL_SMTP_PASSWORD") or email.password
    push = PushConfig(markdown=push_raw.get("markdown", True), email=email)

    # 标签只来自 config.yaml；代码不内置任何领域分类，仅保证有 "其他" 兜底
    tags = [str(t) for t in (raw.get("tags") or [])]
    if "其他" not in tags:
        tags.append("其他")

    return Config(
        arxiv=arxiv,
        interests=raw.get("interests", "").strip(),
        relevance_threshold=int(raw.get("relevance_threshold", 6)),
        max_summarize=int(raw.get("max_summarize", 20)),
        tags=tags,
        fetch_affiliations=bool(raw.get("fetch_affiliations", True)),
        affiliation_pdf_chars=int(raw.get("affiliation_pdf_chars", 1800)),
        max_authors_shown=int(raw.get("max_authors_shown", 6)),
        summarize_fulltext=bool(raw.get("summarize_fulltext", True)),
        fulltext_max_chars=int(raw.get("fulltext_max_chars", 100000)),
        trend_summary=bool(raw.get("trend_summary", True)),
        llm=llm,
        push=push,
    )
