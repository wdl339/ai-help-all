"""启动本地实时仪表盘网页。

用法：
    python serve.py                 # 默认 127.0.0.1:8000
    python serve.py --port 8080
    python serve.py -c my.yaml --host 0.0.0.0

在远程开发环境里：打开 IDE 顶部的「端口 / Ports」面板，转发本服务的端口
（多数 IDE 会自动检测并转发；若没有，手动「添加端口」填 8000 即可），
然后点该端口的地址在浏览器中打开，即可边跑边看爬取 / 打分 / 总结的全过程。
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
    print(f"仪表盘已启动，监听端口 {args.port}（host={args.host}）")
    print(f"  本地访问: http://127.0.0.1:{args.port}")
    print("  远程环境: 在 IDE 的「端口/Ports」面板转发端口 "
          f"{args.port} 后，点其地址在浏览器打开。")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
