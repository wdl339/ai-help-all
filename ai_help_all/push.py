"""推送：生成 markdown 日报，可选邮件发送(HTML)。"""
from __future__ import annotations

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from .arxiv_crawler import Paper
from .config import Config


def build_markdown(papers: list[Paper], date_str: str) -> str:
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
        lines.append(f"- **作者**：{', '.join(p.authors[:6])}{' 等' if len(p.authors) > 6 else ''}")
        lines.append(f"- **分类**：{', '.join(p.categories)}")
        lines.append(f"- **链接**：[摘要页]({p.entry_url})　|　[PDF]({p.pdf_url})")
        lines.append("")
        lines.append(p.summary or "(无总结)")
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


def _markdown_to_simple_html(papers: list[Paper], date_str: str) -> str:
    parts = [
        f"<h1>arxiv 每日论文日报 · {date_str}</h1>",
        f"<p>共筛选出 <b>{len(papers)}</b> 篇相关论文。</p>",
    ]
    for i, p in enumerate(papers, 1):
        summary_html = (p.summary or "").replace("\n", "<br>")
        parts.append(
            f"<h2>{i}. {p.title}</h2>"
            f"<p><b>相关性</b>：{p.score}/10　{p.reason}</p>"
            f"<p><b>作者</b>：{', '.join(p.authors[:6])}</p>"
            f"<p><b>链接</b>：<a href='{p.entry_url}'>摘要页</a> | "
            f"<a href='{p.pdf_url}'>PDF</a></p>"
            f"<p>{summary_html}</p><hr>"
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
    html = _markdown_to_simple_html(papers, date_str)
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


def push_all(cfg: Config, papers: list[Paper]) -> Path | None:
    date_str = datetime.now().strftime("%Y-%m-%d")
    md_path = None
    if cfg.push.markdown:
        content = build_markdown(papers, date_str)
        md_path = write_markdown(content, "digests", date_str)
        print(f"  [推送] markdown 日报已生成: {md_path}")
    send_email(cfg, papers, date_str)
    return md_path
