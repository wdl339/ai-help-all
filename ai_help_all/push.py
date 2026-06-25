"""推送：生成 markdown / JSON 日报，可选邮件发送(HTML)。"""
from __future__ import annotations

import json
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as _escape
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


# 与网页仪表盘一致的配色（index.html 的 CSS 变量；邮件不支持变量，这里取字面值）
_C = {
    "bg": "#f6f7f9", "panel": "#ffffff", "panel2": "#eef1f5", "border": "#e2e6ec",
    "fg": "#1c2128", "muted": "#667085", "accent": "#2f6feb", "accent2": "#7c5cff",
    "green": "#1a7f37", "yellow": "#b8860b", "lo": "#5a6472",
}
_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',"
         "'Microsoft YaHei',sans-serif")


def _render_summary_html(text: str) -> str:
    """与网页 renderSummary 一致：转义 -> **加粗** -> 换行 <br>。"""
    h = _escape(text or "")
    h = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h)
    return h.replace("\n", "<br>")


def _score_badge_bg(score: int) -> str:
    """分数徽章底色，阈值同网页 badgeClass（>=8 绿 / >=6 黄 / 其余灰）。"""
    if score >= 8:
        return _C["green"]
    if score >= 6:
        return _C["yellow"]
    return _C["lo"]


def _email_card(i: int, p: Paper, max_authors: int) -> str:
    """单篇论文卡片（内联样式，仿网页 .card）。"""
    title = _escape(p.title)
    tag = _escape(p.tag or "其他")
    authors = ", ".join(_escape(a) for a in p.authors[:max_authors])
    if len(p.authors) > max_authors:
        authors += " 等"
    cats = _escape(", ".join(p.categories))

    # 总结区：成功=正常色，失败/未生成=灰色提示
    s = (p.summary or "").strip()
    if s and not s.startswith("(总结"):
        summary_inner = _render_summary_html(s)
        summary_color = _C["fg"]
    else:
        summary_inner = _escape(s) if s else "未生成 AI 总结"
        summary_color = _C["muted"]

    aff_row = ""
    if p.affiliations:
        aff_row = (
            f'<div style="color:{_C["muted"]};font-size:12.5px;margin:4px 0;">'
            f'发表机构：{_escape("；".join(p.affiliations))}</div>'
        )

    return f"""
    <div style="background:{_C['panel']};border:1px solid {_C['border']};border-radius:12px;
                padding:16px 18px;margin-bottom:14px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="border-collapse:collapse;">
        <tr>
          <td valign="top" style="width:34px;padding-right:10px;">
            <span style="display:inline-block;background:{_score_badge_bg(p.score)};color:#fff;
                         font-weight:700;border-radius:6px;padding:2px 8px;font-size:13px;">{p.score}</span>
          </td>
          <td valign="top">
            <span style="font-size:15.5px;font-weight:650;color:{_C['fg']};line-height:1.4;">{i}. {title}</span>
          </td>
          <td valign="top" align="right" style="padding-left:10px;white-space:nowrap;">
            <span style="display:inline-block;background:{_C['panel2']};border:1px solid {_C['border']};
                         color:{_C['accent']};border-radius:999px;padding:2px 10px;font-size:12px;">{tag}</span>
          </td>
        </tr>
      </table>
      <div style="color:{_C['accent']};font-size:13px;margin:8px 0 6px;">{_escape(p.reason or "")}</div>
      <div style="color:{_C['muted']};font-size:12.5px;margin:4px 0;">{authors}</div>
      {aff_row}
      <div style="color:{_C['muted']};font-size:12.5px;margin:4px 0;">{cats} ·
        <a href="{_escape(p.entry_url)}" style="color:{_C['accent']};text-decoration:none;">摘要页</a> ·
        <a href="{_escape(p.pdf_url)}" style="color:{_C['accent']};text-decoration:none;">PDF</a>
      </div>
      <div style="background:{_C['panel2']};border-radius:8px;padding:12px 14px;font-size:13.5px;
                  color:{summary_color};margin-top:10px;line-height:1.7;">{summary_inner}</div>
    </div>"""


def _build_email_html(papers: list[Paper], date_str: str, max_authors: int = 6) -> str:
    """生成与网页仪表盘同风格的 HTML 邮件（全内联样式，兼容主流邮件客户端）。"""
    cards = "".join(_email_card(i, p, max_authors) for i, p in enumerate(papers, 1))
    header = f"""
    <div style="background:{_C['accent']};
                background:linear-gradient(135deg,{_C['accent']},{_C['accent2']});
                border-radius:14px;padding:20px 24px;margin-bottom:18px;color:#fff;">
      <div style="font-size:12.5px;font-weight:700;letter-spacing:.5px;opacity:.92;">Aha · AI helps all</div>
      <div style="font-size:21px;font-weight:800;margin-top:4px;">arXiv 论文日报 · {date_str}</div>
      <div style="font-size:13px;opacity:.92;margin-top:6px;">
        共筛选出 {len(papers)} 篇相关论文（按相关性从高到低）</div>
    </div>"""
    footer = (
        f'<div style="color:{_C["muted"]};font-size:12px;text-align:center;'
        f'margin-top:18px;padding-top:14px;border-top:1px solid {_C["border"]};">'
        f'本邮件由 ai-help-all 自动生成 · {date_str}</div>'
    )
    body = f"""
    <div style="margin:0;padding:20px;background:{_C['bg']};">
      <div style="max-width:720px;margin:0 auto;font-family:{_FONT};
                  color:{_C['fg']};font-size:14px;line-height:1.6;">
        {header}{cards}{footer}
      </div>
    </div>"""
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body style='margin:0;'>{body}</body></html>"


class EmailNotConfigured(Exception):
    """SMTP 必填项缺失（host/username/password/to_addrs）。"""


def deliver_digest_email(cfg: Config, papers: list[Paper], date_str: str) -> list[str]:
    """构建并发送日报邮件，返回收件人列表。

    与 send_email 不同：这里**会抛异常**——配置不完整抛 EmailNotConfigured，
    SMTP 登录/投递失败抛原异常。供网页「发送邮件」按钮调用以获得明确反馈。
    """
    ec = cfg.push.email
    if not (ec.smtp_host and ec.username and ec.password and ec.to_addrs):
        raise EmailNotConfigured(
            "邮件配置不完整（需 smtp_host/username/password/to_addrs，授权码可放 .env）"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[arxiv日报] {date_str} 共 {len(papers)} 篇相关论文"
    msg["From"] = ec.from_addr or ec.username
    msg["To"] = ", ".join(ec.to_addrs)
    html = _build_email_html(papers, date_str, cfg.max_authors_shown)
    msg.attach(MIMEText(html, "html", "utf-8"))

    if ec.use_ssl:
        server = smtplib.SMTP_SSL(ec.smtp_host, ec.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(ec.smtp_host, ec.smtp_port, timeout=30)
        server.starttls()
    with server:
        server.login(ec.username, ec.password)
        server.sendmail(msg["From"], ec.to_addrs, msg.as_string())
    return list(ec.to_addrs)


def send_email(cfg: Config, papers: list[Paper], date_str: str, send: bool = False) -> None:
    """流水线用：是否发送由 send 决定（默认不发）；失败只打印、不中断运行。"""
    if not send:
        return
    try:
        to = deliver_digest_email(cfg, papers, date_str)
        print(f"  [推送] 邮件已发送至 {', '.join(to)}")
    except EmailNotConfigured as e:
        print(f"  [推送] {e}，跳过邮件发送。")
    except Exception as e:  # noqa: BLE001 - 邮件失败不影响流水线
        print(f"  [推送] 邮件发送失败: {e}")


def push_all(cfg: Config, papers: list[Paper], date_str: str | None = None,
             *, email: bool = False) -> dict:
    """生成日报并推送，返回产物路径信息。date_str 为日报日期标签。

    email: 运行时邮件开关（默认 False，不发邮件）；为 True 才尝试按 config 的
    SMTP 参数发送当天日报。
    """
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

    send_email(cfg, papers, date_str, send=email)
    return result
