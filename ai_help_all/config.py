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
    filter_model: str = "deepseek-chat"
    summarize_model: str = "deepseek-chat"
    requests_per_minute: int = 9
    filter_batch_size: int = 20
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
    max_summarize: int = 15
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
        max_summarize=int(raw.get("max_summarize", 15)),
        llm=llm,
        push=push,
    )
