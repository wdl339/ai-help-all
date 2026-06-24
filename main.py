"""ai-help-all 入口：爬取 arxiv -> 去重 -> LLM 筛选 -> 总结 -> 推送。

用法：
    python main.py                  # 用 config.yaml 跑完整流程
    python main.py -c my.yaml       # 指定配置文件
    python main.py --dry-run        # 只爬取并打印候选论文，不调用 LLM、不推送（不耗 token）
    python main.py --no-dedup       # 不跳过历史已推送过的论文
"""
from __future__ import annotations

import argparse
import sys

from ai_help_all.arxiv_crawler import fetch_recent_papers
from ai_help_all.config import load_config
from ai_help_all.filter import score_papers, select_relevant
from ai_help_all.llm_client import LLMClient
from ai_help_all.push import push_all
from ai_help_all.seen import filter_unseen, load_seen, save_seen
from ai_help_all.summarizer import summarize_all


def run(config_path: str, dedup: bool = True, dry_run: bool = False) -> int:
    cfg = load_config(config_path)

    print(f"[1/5] 爬取 arxiv（分类: {', '.join(cfg.arxiv.categories)}，近 {cfg.arxiv.days_back} 天）...")
    papers = fetch_recent_papers(cfg.arxiv)
    print(f"      共抓取 {len(papers)} 篇候选。")
    if not papers:
        print("没有抓到论文，结束。")
        return 0

    seen: set[str] = set()
    if dedup:
        seen = load_seen()
        before = len(papers)
        papers = filter_unseen(papers, seen)
        print(f"[2/5] 去重：过滤掉 {before - len(papers)} 篇历史已推送，剩 {len(papers)} 篇。")
    else:
        print("[2/5] 跳过去重。")

    if not papers:
        print("去重后无新论文，结束。")
        return 0

    if dry_run:
        print("[dry-run] 仅展示候选论文（不调用 LLM、不推送）：")
        for p in papers[:50]:
            print(f"  - [{', '.join(p.categories)}] {p.title}")
        print(f"\n(dry-run) 共 {len(papers)} 篇候选。")
        return 0

    llm = LLMClient(cfg.llm)

    print(f"[3/5] LLM 相关性筛选（阈值 >= {cfg.relevance_threshold}）...")
    score_papers(llm, cfg, papers)
    selected = select_relevant(cfg, papers)
    print(f"      命中 {len(selected)} 篇相关论文。")

    if not selected:
        print("没有达到相关性阈值的论文，结束。")
        if dedup:
            save_seen(seen | {p.short_id for p in papers})
        return 0

    print("[4/5] 生成总结 ...")
    summarize_all(llm, cfg, selected)

    print("[5/5] 推送 ...")
    push_all(cfg, selected)

    if dedup:
        save_seen(seen | {p.short_id for p in papers})

    print("完成 ✅")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ai-help-all: 每日 arxiv 论文推送")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--no-dedup", action="store_true", help="不跳过历史已推送过的论文")
    parser.add_argument("--dry-run", action="store_true", help="只爬取并打印候选，不调用 LLM、不推送")
    args = parser.parse_args()
    try:
        return run(args.config, dedup=not args.no_dedup, dry_run=args.dry_run)
    except (FileNotFoundError, ValueError) as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
