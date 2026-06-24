"""ai-help-all 入口：爬取 arxiv -> 去重 -> 并发 LLM 筛选 -> 总结 -> 推送。

用法：
    python main.py                  # 用 config.yaml 跑完整流程
    python main.py -c my.yaml       # 指定配置文件
    python main.py --dry-run        # 只爬取并打印候选论文，不调用 LLM、不推送（不耗 token）
    python main.py --no-dedup       # 不跳过历史已推送过的论文
    python main.py --list-models    # 列出可调用模型(校验 api-key / 连通性)

启动本地实时仪表盘网页见 serve.py：python serve.py
"""
from __future__ import annotations

import argparse
import sys

from ai_help_all.config import load_config
from ai_help_all.events import make_print_emitter
from ai_help_all.llm_client import LLMClient
from ai_help_all.pipeline import run_pipeline


def list_models(config_path: str) -> int:
    cfg = load_config(config_path)
    llm = LLMClient(cfg.llm)
    print(f"连接 {cfg.llm.base_url} ...")
    try:
        models = llm.list_models()
    except Exception as e:  # noqa: BLE001
        print(f"获取模型列表失败（请检查 api-key 是否正确、网络是否可达）: {e}", file=sys.stderr)
        return 1
    print("可调用模型 id：")
    for m in models:
        print(f"  - {m}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ai-help-all: 每日 arxiv 论文推送")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--no-dedup", action="store_true", help="不跳过历史已推送过的论文")
    parser.add_argument("--dry-run", action="store_true", help="只爬取并打印候选，不调用 LLM、不推送")
    parser.add_argument("--list-models", action="store_true", help="列出可调用模型并退出")
    args = parser.parse_args()
    try:
        if args.list_models:
            return list_models(args.config)
        cfg = load_config(args.config)
        run_pipeline(cfg, make_print_emitter(), dedup=not args.no_dedup, dry_run=args.dry_run)
        return 0
    except (FileNotFoundError, ValueError) as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
