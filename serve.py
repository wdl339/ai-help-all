"""启动本地实时仪表盘网页。

用法：
    python serve.py                 # 默认 http://127.0.0.1:8000
    python serve.py --port 8080
    python serve.py -c my.yaml --host 0.0.0.0

在 Cursor / VSCode 里可用命令面板的 "Simple Browser: Show" 打开该地址，
即可在 IDE 内边跑边看爬取 / 打分 / 总结的全过程。
"""
from __future__ import annotations

import argparse

import uvicorn

from ai_help_all.webapp import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="ai-help-all 本地仪表盘")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="端口")
    args = parser.parse_args()

    app = create_app(args.config)
    print(f"仪表盘已启动 → http://{args.host}:{args.port}")
    print("在 Cursor 命令面板运行 'Simple Browser: Show' 并粘贴上面地址即可在 IDE 内查看。")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
