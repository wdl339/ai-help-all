"""推送：生成 markdown / JSON 日报，可选邮件发送(HTML)。"""
from __future__ import annotations

import json
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from .arxiv_crawler import Paper
from .config import Config


def build_markdown(papers: list[Paper], date_str: str, max_authors: int = 6) -> str:
    lines = [
        f"# arxiv 每日论文日报 · {date_str}",
        "",
        f"共筛选出 **{len(papers)}** 篇相关论文（按相关性从高到低排序）。",
        "",
    ]
    for i, p in enumerate(papers, 1):
        lines.append(f"## {i}. {p.title}")
        lines.append("")
        lines.append(f"- **相关性评分**：{p.score}/10　{p.reason}")
        lines.append(f"- **作者**：{', '.join(p.authors[:max_authors])}{' 等' if len(p.authors) > max_authors else ''}")
        if p.affiliations:
            lines.append(f"- **发表机构**：{'；'.join(p.affiliations)}")
        lines.append(f"- **分类**：{', '.join(p.categories)}")
        lines.append(f"- **链接**：[摘要页]({p.entry_url})　|　[PDF]({p.pdf_url})")
        lines.append("")
        lines.append(p.summary or "_（未生成 AI 总结，可在仪表盘按需生成）_")
        lines.append("")
        # 原始摘要 + 中文翻译（markdown 用 <details> 折叠）
        lines.append("<details><summary>展开原始摘要 / 中文翻译</summary>")
        lines.append("")
        if p.abstract_zh:
            lines.append(f"**摘要（中文）**：{p.abstract_zh}")
            lines.append("")
        lines.append(f"**Abstract (EN)**: {p.abstract}")
        lines.append("")
        lines.append("</details>")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def write_markdown(content: str, out_dir: str | Path, date_str: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"digest-{date_str}.md"
    path.write_text(content, encoding="utf-8")
    return path


def write_json(papers: list[Paper], out_dir: str | Path, date_str: str) -> Path:
    """写出当日 JSON 日报，并维护一个 index.json 历史列表（供网页读取）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(papers),
        "papers": [p.to_dict() for p in papers],
    }
    path = out_dir / f"digest-{date_str}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 维护历史索引
    index_path = out_dir / "index.json"
    history: list[dict] = []
    if index_path.exists():
        try:
            history = json.loads(index_path.read_text(encoding="utf-8")).get("digests", [])
        except (json.JSONDecodeError, OSError):
            history = []
    history = [h for h in history if h.get("date") != date_str]
    history.append({"date": date_str, "count": len(papers), "file": path.name})
    history.sort(key=lambda h: h["date"], reverse=True)
    index_path.write_text(
        json.dumps({"digests": history}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def _markdown_to_simple_html(papers: list[Paper], date_str: str, max_authors: int = 6) -> str:
    parts = [
        f"<h1>arxiv 每日论文日报 · {date_str}</h1>",
        f"<p>共筛选出 <b>{len(papers)}</b> 篇相关论文。</p>",
    ]
    for i, p in enumerate(papers, 1):
        summary_html = (p.summary or "").replace("\n", "<br>")
        aff_html = (
            f"<p><b>发表机构</b>：{'；'.join(p.affiliations)}</p>" if p.affiliations else ""
        )
        abs_zh = f"<p><b>摘要(中文)</b>：{p.abstract_zh}</p>" if p.abstract_zh else ""
        parts.append(
            f"<h2>{i}. {p.title}</h2>"
            f"<p><b>相关性</b>：{p.score}/10　{p.reason}</p>"
            f"<p><b>作者</b>：{', '.join(p.authors[:max_authors])}</p>"
            f"{aff_html}"
            f"<p><b>链接</b>：<a href='{p.entry_url}'>摘要页</a> | "
            f"<a href='{p.pdf_url}'>PDF</a></p>"
            f"<p>{summary_html}</p>"
            f"<details><summary>原始摘要 / 中文翻译</summary>{abs_zh}"
            f"<p><b>Abstract</b>: {p.abstract}</p></details><hr>"
        )
    return "<html><body>" + "".join(parts) + "</body></html>"


def send_email(cfg: Config, papers: list[Paper], date_str: str) -> None:
    ec = cfg.push.email
    if not ec.enabled:
        return
    if not (ec.smtp_host and ec.username and ec.password and ec.to_addrs):
        print("  [推送] 邮件配置不完整，跳过邮件发送。")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[arxiv日报] {date_str} 共 {len(papers)} 篇相关论文"
    msg["From"] = ec.from_addr or ec.username
    msg["To"] = ", ".join(ec.to_addrs)
    html = _markdown_to_simple_html(papers, date_str, cfg.max_authors_shown)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if ec.use_ssl:
            server = smtplib.SMTP_SSL(ec.smtp_host, ec.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(ec.smtp_host, ec.smtp_port, timeout=30)
            server.starttls()
        with server:
            server.login(ec.username, ec.password)
            server.sendmail(msg["From"], ec.to_addrs, msg.as_string())
        print(f"  [推送] 邮件已发送至 {', '.join(ec.to_addrs)}")
    except Exception as e:  # noqa: BLE001
        print(f"  [推送] 邮件发送失败: {e}")


def push_all(cfg: Config, papers: list[Paper], date_str: str | None = None) -> dict:
    """生成日报并推送，返回产物路径信息。date_str 为日报日期标签。"""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    result: dict = {"date": date_str, "markdown": None, "json": None}

    # JSON 始终生成（网页读取）
    json_path = write_json(papers, "digests", date_str)
    result["json"] = str(json_path)

    if cfg.push.markdown:
        content = build_markdown(papers, date_str, cfg.max_authors_shown)
        md_path = write_markdown(content, "digests", date_str)
        result["markdown"] = str(md_path)

    send_email(cfg, papers, date_str)
    return result
