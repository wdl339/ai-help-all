"""本地实时仪表盘：FastAPI + SSE，边跑流水线边在网页上看全过程。

设计要点（解决"刷新/切历史就看不到进度"）：
- 运行流水线与订阅进度解耦：后台线程独立运行，事件写入缓冲区；
- 任何时候连上 /api/stream 都会先"回放"本次运行已发生的事件，再继续推实时事件；
- 因此刷新页面、切去看历史、再点运行，都能随时重新接回正在进行的进度；
- 每个事件带递增 seq，前端据此去重（兼容 EventSource 自动重连导致的重复回放）。
"""
from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .arxiv_crawler import Paper
from .config import load_config
from .llm_client import LLMClient
from .pipeline import run_pipeline
from .summarizer import summarize_paper

_STATIC_DIR = Path(__file__).parent / "static"
_DIGESTS_DIR = Path("digests")


class RunManager:
    """单例运行管理器：一个进程同一时刻最多一个运行，事件可被多次/延迟订阅。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict] = []          # 本次运行的事件缓冲（用于回放）
        self.subscribers: list[queue.Queue] = []
        self.running = False
        self.run_id = 0
        self._seq = 0

    def _emit(self, event_type: str, payload: dict) -> None:
        with self.lock:
            ev = {"seq": self._seq, "type": event_type, "payload": payload}
            self._seq += 1
            self.events.append(ev)
            for q in self.subscribers:
                q.put(ev)

    def start(self, config_path: str, *, refresh: bool, dry_run: bool) -> bool:
        """启动一次运行；若已有运行返回 False（调用方应改为订阅 /api/stream）。"""
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.run_id += 1
            self.events = []
            self._seq = 0

        def worker() -> None:
            try:
                cfg = load_config(config_path)
                run_pipeline(cfg, self._emit, dedup=not refresh, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001
                self._emit("error", {"message": f"运行失败: {e}"})
            finally:
                self._emit("_end", {})
                with self.lock:
                    self.running = False

        threading.Thread(target=worker, daemon=True).start()
        return True

    def subscribe(self) -> tuple[list[dict], queue.Queue | None]:
        """返回 (已发生事件的快照, 实时队列或None)。原子地完成快照+注册避免漏/重。"""
        with self.lock:
            backlog = list(self.events)
            q: queue.Queue | None = None
            if self.running:
                q = queue.Queue()
                self.subscribers.append(q)
            return backlog, q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def status(self) -> dict:
        with self.lock:
            return {"running": self.running, "run_id": self.run_id, "events": len(self.events)}


def _paper_from_dict(d: dict) -> Paper:
    """从日报 JSON 里的论文 dict 重建 Paper（用于单篇重新总结）。"""
    def _dt(s):
        try:
            return datetime.fromisoformat(s) if s else datetime.now()
        except (ValueError, TypeError):
            return datetime.now()

    return Paper(
        arxiv_id=d.get("arxiv_id", ""),
        title=d.get("title", ""),
        authors=d.get("authors", []),
        abstract=d.get("abstract", ""),
        categories=d.get("categories", []),
        published=_dt(d.get("published")),
        updated=_dt(d.get("updated")),
        pdf_url=d.get("pdf_url", ""),
        entry_url=d.get("entry_url", ""),
        score=d.get("score", 0),
        reason=d.get("reason", ""),
    )


def create_app(config_path: str = "config.yaml") -> FastAPI:
    app = FastAPI(title="ai-help-all dashboard")
    manager = RunManager()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/config")
    def get_config() -> JSONResponse:
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
        safe = "".join(c for c in date if c.isdigit() or c == "-")
        path = _DIGESTS_DIR / f"digest-{safe}.json"
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/status")
    def status() -> JSONResponse:
        return JSONResponse(manager.status())

    @app.get("/api/resummarize")
    def resummarize(short_id: str, date: str = "") -> JSONResponse:
        """对某一天日报里的单篇论文重新生成总结（用于失败/超时后的手动重试）。"""
        safe = "".join(c for c in (date or "") if c.isdigit() or c == "-")
        if not safe:
            safe = datetime.now().strftime("%Y-%m-%d")
        path = _DIGESTS_DIR / f"digest-{safe}.json"
        if not path.exists():
            return JSONResponse(
                {"error": "该日期日报尚未生成（如本次运行还没结束，请等结束后再重试）"},
                status_code=404,
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return JSONResponse({"error": f"读取日报失败: {e}"}, status_code=500)

        pd = next((p for p in data.get("papers", []) if p.get("short_id") == short_id), None)
        if pd is None:
            return JSONResponse({"error": "日报中未找到该论文"}, status_code=404)

        try:
            cfg = load_config(config_path)
            paper = _paper_from_dict(pd)
            summarize_paper(LLMClient(cfg.llm), cfg, paper)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"重新总结失败: {e}"}, status_code=500)

        pd["summary"] = paper.summary
        pd["abstract_zh"] = paper.abstract_zh
        pd["affiliations"] = paper.affiliations
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            return JSONResponse({"error": f"写回日报失败: {e}"}, status_code=500)

        return JSONResponse({
            "ok": True,
            "short_id": short_id,
            "summary": paper.summary,
            "abstract_zh": paper.abstract_zh,
            "affiliations": paper.affiliations,
        })

    @app.get("/api/start")
    def start(dry_run: bool = False, refresh: bool = False) -> JSONResponse:
        started = manager.start(config_path, refresh=refresh, dry_run=dry_run)
        return JSONResponse({"started": started, **manager.status()})

    @app.get("/api/stream")
    def stream() -> StreamingResponse:
        backlog, q = manager.subscribe()

        def gen():
            # 1) 先回放本次运行已发生的事件
            for ev in backlog:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            # 2) 若当前没有在运行，补一个结束标记后收尾
            if q is None:
                if not backlog or backlog[-1]["type"] != "_end":
                    yield f"data: {json.dumps({'seq': -1, 'type': '_end', 'payload': {}})}\n\n"
                return
            # 3) 继续推实时事件
            try:
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        yield ": ping\n\n"  # 心跳保活
                        continue
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if ev["type"] == "_end":
                        break
            finally:
                manager.unsubscribe(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app
