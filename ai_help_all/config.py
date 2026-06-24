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
    max_results: int = 200


@dataclass
class LLMConfig:
    base_url: str = "https://models.sjtu.edu.cn/api/v1"
    api_key: str = ""
    # 筛选模型：glm-5.1 直接返回 JSON、稳定可靠，适合批量结构化打分
    filter_model: str = "glm-5.1"
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
    max_retries: int = 3
    # 一次筛选 prompt 里塞多少篇论文
    filter_batch_size: int = 20
    # 单次请求的最大输出 token（思考模型会先消耗 token 做推理，需留足余量）
    filter_max_tokens: int = 2048
    # 总结要同时产出 中文总结 + 摘要翻译 + 作者单位，需更大余量
    summarize_max_tokens: int = 3072
    language: str = "中文"


@dataclass
class EmailConfig:
    enabled: bool = False
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
    max_summarize: int = 20
    # 是否下载 PDF 首页提取作者单位/发表机构（关掉可省下载时间）
    fetch_affiliations: bool = True
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
    email_raw = push_raw.get("email") or {}
    email = EmailConfig(**email_raw)
    email.password = os.getenv("AI_HELP_ALL_SMTP_PASSWORD") or email.password
    push = PushConfig(markdown=push_raw.get("markdown", True), email=email)

    return Config(
        arxiv=arxiv,
        interests=raw.get("interests", "").strip(),
        relevance_threshold=int(raw.get("relevance_threshold", 6)),
        max_summarize=int(raw.get("max_summarize", 20)),
        fetch_affiliations=bool(raw.get("fetch_affiliations", True)),
        llm=llm,
        push=push,
    )
