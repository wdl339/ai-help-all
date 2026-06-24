"""本地实时仪表盘：FastAPI + SSE，边跑流水线边在网页上看全过程。

启动见根目录 serve.py。页面通过 EventSource 订阅 /api/run，
后台线程跑 run_pipeline，并把事件实时推到浏览器。
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import load_config
from .pipeline import run_pipeline

_STATIC_DIR = Path(__file__).parent / "static"
_DIGESTS_DIR = Path("digests")

# 同一时刻只允许一个运行
_run_lock = threading.Lock()


def create_app(config_path: str = "config.yaml") -> FastAPI:
    app = FastAPI(title="ai-help-all dashboard")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = _STATIC_DIR / "index.html"
        return html.read_text(encoding="utf-8")

    @app.get("/api/config")
    def get_config() -> JSONResponse:
        """给页面展示当前配置概要（不含密钥）。"""
        try:
            cfg = load_config(config_path)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({
            "categories": cfg.arxiv.categories,
            "days_back": cfg.arxiv.days_back,
            "max_results": cfg.arxiv.max_results,
            "relevance_threshold": cfg.relevance_threshold,
            "max_summarize": cfg.max_summarize,
            "filter_model": cfg.llm.filter_model,
            "summarize_model": cfg.llm.summarize_model,
            "max_concurrency": cfg.llm.max_concurrency,
            "interests": cfg.interests,
        })

    @app.get("/api/history")
    def history() -> JSONResponse:
        index_path = _DIGESTS_DIR / "index.json"
        if not index_path.exists():
            return JSONResponse({"digests": []})
        try:
            return JSONResponse(json.loads(index_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            return JSONResponse({"digests": [], "error": str(e)})

    @app.get("/api/digest/{date}")
    def digest(date: str) -> JSONResponse:
        # 防目录穿越：只允许日期形式
        safe = "".join(c for c in date if c.isdigit() or c == "-")
        path = _DIGESTS_DIR / f"digest-{safe}.json"
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/run")
    def run(dry_run: bool = False, dedup: bool = True) -> StreamingResponse:
        q: queue.Queue = queue.Queue()

        def emit(event_type: str, payload: dict) -> None:
            q.put({"type": event_type, "payload": payload})

        def worker() -> None:
            if not _run_lock.acquire(blocking=False):
                q.put({"type": "error", "payload": {"message": "已有一个任务在运行，请稍候。"}})
                q.put({"type": "_end", "payload": {}})
                return
            try:
                cfg = load_config(config_path)
                run_pipeline(cfg, emit, dedup=dedup, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001
                q.put({"type": "error", "payload": {"message": f"运行失败: {e}"}})
            finally:
                _run_lock.release()
                q.put({"type": "_end", "payload": {}})

        threading.Thread(target=worker, daemon=True).start()

        def stream():
            # 先发一个 hello，便于前端确认连接
            yield f"data: {json.dumps({'type': 'hello', 'payload': {}})}\n\n"
            while True:
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"  # 心跳，保持连接
                    continue
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item["type"] == "_end":
                    break

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app
