# AI helps all

爬取 arxiv 每日最新论文，用 LLM 按你的研究兴趣打分并筛选，生成总结并推送。
提供一个**本地实时仪表盘网页**，可以看到爬取 / 打分 / 总结的全过程。

## 关于本项目

本项目几乎完全是与 **Claude Opus 4.8** 结对「vibe coding」迭代出来的：从整体架构、模块拆分，到实时仪表盘、并发限速、邮件推送等细节，大多在与模型的反复对话中设计、实现并打磨而成。

开发过程中也参考了一些同类开源项目的实现思路：

- [q1w2e3r4-1/arxiv-daily-researcher](https://github.com/q1w2e3r4-1/arxiv-daily-researcher)
- [RunRiotComeOn/arXiv-Daily-Summarizer](https://github.com/RunRiotComeOn/arXiv-Daily-Summarizer)
- [ZhuYizhou2333/ArXiv-Pusher](https://github.com/ZhuYizhou2333/ArXiv-Pusher)
- [JoeLeelyf/customize-arxiv-daily](https://github.com/JoeLeelyf/customize-arxiv-daily)
- [ziwenhahaha/daily-paper-reader](https://github.com/ziwenhahaha/daily-paper-reader)
- [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily)

> 致谢：感谢上述项目作者的开源分享，也感谢 [arXiv](https://arxiv.org/) 提供开放的论文库。

## 功能流程

```
爬取 arxiv  →  历史去重  →  LLM 打分并筛选  →  高分文章逐篇总结  →  今日趋势综述  →  推送
```

每篇入选论文会产出：中文结构化总结、**作者单位/发表机构**、
**英文原始摘要 + 中文翻译**。

## 目录结构

```
ai-help-all/
├── main.py                 # CLI 入口（命令行跑完整流程）
├── serve.py                # 启动本地实时仪表盘网页
├── ai_help_all/
│   ├── config.py           # 配置加载（密钥优先从环境变量取）
│   ├── arxiv_crawler.py    # arxiv 爬取
│   ├── llm_client.py       # OpenAI 兼容客户端 + 双限速 + 重试
│   ├── filter.py           # LLM 并发批量相关性打分 + 阈值筛选
│   ├── summarizer.py       # 并发逐篇中文结构化总结
│   ├── pipeline.py         # 流水线编排（事件驱动，CLI / 网页共用）
│   ├── events.py           # 事件回调（进度实时上报）
│   ├── seen.py             # 已推送论文去重
│   ├── push.py             # markdown / JSON 日报 + 邮件推送
│   ├── webapp.py           # FastAPI + SSE 仪表盘后端
│   └── static/index.html   # 仪表盘前端页面
├── config.example.yaml     # 配置示例（复制为 config.yaml 后使用）
├── .env.example            # 密钥示例（复制为 .env 后使用）
├── requirements.txt
└── .gitignore              # 已忽略 config.yaml / .env / digests / seen_papers.json
```

## 实时仪表盘

```bash
python serve.py            # 默认监听 127.0.0.1:8000
```

打开方式（任选其一）：
- **远程开发环境**（本项目常用）：打开 IDE 顶部的 **「端口 / Ports」** 面板，转发本服务端口
  （多数 IDE 会自动检测；若没有就「添加端口」填 `8000`），点该端口地址在浏览器打开。
- **本地**：直接浏览器访问 `http://127.0.0.1:8000`，或用编辑器命令面板的 “Simple Browser: Show”。

点「运行」后可实时看到：阶段进度、打分进度条与实时打分流、入选论文的卡片。顶部工具栏可勾选 **“重新生成”**（忽略已存在的日报，重新运行流程）、查看历史日报。
即使运行中途刷新页面、查看历史日报，都能重新接回正在进行的进度。

## 安全说明（重要）

> 本仓库会 push 到 GitHub，**真实的 API key、邮箱密码绝不能提交**。

- 真实密钥放在 `.env` 或 `config.yaml`，这两个文件已被 `.gitignore` 忽略。
- 仓库里只保留 `*.example` 示例文件。
- 推荐方式：把 key 放进环境变量 `AI_HELP_ALL_API_KEY`，代码会优先读取它。

## 快速开始

```bash
cd ai-help-all

# 1. 安装依赖（建议用虚拟环境）
pip install -r requirements.txt

# 2. 准备配置
cp config.example.yaml config.yaml      # 改你的研究兴趣、分类、阈值
cp .env.example .env                     # 填入 API key

# 3. 校验密钥与连通性：列出当前可调用的模型 id
python main.py --list-models

# 4. 先试跑：只爬取并打印候选论文，不调用 LLM、不推送（不耗 token）
python main.py --dry-run

# 5. 正式跑：生成 digests/digest-YYYY-MM-DD.{md,json}（默认不发邮件；要发加 --email）
python main.py

# 6. 重新生成：忽略历史去重，重跑今天（默认不会重复处理已处理过的论文）
python main.py --refresh
```

> 若 `--list-models` 报连接/认证错误，多半是网络不可达或 api-key 问题。

## 配置要点（`config.yaml`）

- `arxiv.categories`：关注的 arxiv 分类，如 `cs.AI / cs.CL / cs.LG`。
- `arxiv.days_back`：往前看几天（平时 1，周一可设 3 把周末补上）。
- `interests`：用自然语言写清你的研究方向，**越具体筛得越准**。
- `tags`：给入选论文打的标签。
- `relevance_threshold`：相关性阈值（1-10），达标才会被总结推送。
- `max_summarize`：每天最多总结几篇。
- `fetch_affiliations`：是否下载 PDF 首页提取作者单位（arxiv API 不提供机构信息，
  且 OpenAlex 等对当天新论文有索引延迟，故从 PDF 首页由 LLM 抽取）。关掉可省下载时间。
- `summarize_fulltext` / `fulltext_max_chars`：总结是否基于全文、以及全文截断长度（打分始终只用摘要）。
- `trend_summary`：是否在每份日报顶部生成「今日速览」趋势综述（默认开；额外 1 次 LLM 调用/天，用总结模型）。
- `llm.filter_model / summarize_model`：可选模型见下表。
- `llm.requests_per_minute / tokens_per_minute`：API 限速参数（默认按额度留了余量，详见「可用模型」）。
- `push.email`：可选邮件推送，默认关闭（详细配置见下方「邮件推送」）。

## 可用模型（SJTU 交我算 API）

API 详情与申请方式见：https://claw.sjtu.edu.cn/guide/sjtu-api/

| 调用名 | 模型 | 上下文 | 侧重 |
|---|---|---|---|
| `glm-5.1` | GLM-5.1 | 128k | 强；直接返回内容，适合批量结构化打分（**默认筛选模型**） |
| `minimax-m2.7` | MiniMax-M2.7 | 192k | 强；**思考模型**，总结质量高（**默认总结模型**，需较大 `max_tokens`） |
| `deepseek-chat` | DeepSeek V3.2 常规 | 32k | 通用文本 |
| `deepseek-reasoner` | DeepSeek V3.2 思考 | 32k | 复杂推理（**不接受 temperature**） |
| `qwen3.5-27b` | Qwen3.5-27B | 256k | 最快、上下文最大，备选筛选模型 |

> 用 `python main.py --list-models` 可查看 api-key 实际可调用的模型 id。
>
> **思考模型**（`minimax-m2.7` / `deepseek-reasoner`）会先花 token 做内部推理再输出。用于**批量筛选**这类长 prompt 任务时，`max_tokens` 不够会被推理吃光、返回空——故筛选默认用更快、直接出内容的 `glm-5.1`，总结才用 `minimax-m2.7`。

### 并发与速率限制

API 额度：每分钟 10 次请求 / 每分钟 100000 token / 每周 10 亿 token。

- **并发**：筛选(各批次)与总结(各论文)用线程池并发提交（`llm.max_concurrency`，默认 8）。单次调用的网络/生成延迟远大于限速间隔，并发把这些延迟重叠起来，实测较串行快数倍。
- **双限速器**：`llm_client.py` 内置滑动窗口限速器，同时约束每分钟请求数与 token；并发再高也不会超额度（超了自动等待）。
- **省请求**：筛选把多篇论文打包进一次请求（`filter_batch_size`，默认 20 篇/次）；每个请求都带 `max_tokens`（`filter_max_tokens / summarize_max_tokens`）。
- DeepSeek V3.2 要求请求必须含 `user` 消息（本项目均满足）。

### Token 用量统计

每次运行结束会统计本次**真实** token 消耗（取自 API 返回的 `usage`，按模型分组），
并按自然日累计到 `digests/usage.json`，便于核对额度（SJTU 每周 10 亿 token）。

- **CLI**：运行结束打印「本次用量：N 次请求，X tokens」。
- **网页**：顶部显示「今日 / 近 7 天」的 token 用量；也可调用 `GET /api/usage?days=7`。

## 邮件推送

可选择把当天入选论文以 **HTML 邮件**发到你的邮箱。**默认不发**，由运行时开关控制：

- **CLI**：加 `--email`（如 `python main.py --email`）
- **网页**：顶部工具栏勾选 **「发送邮件」** 再点运行；查看历史日报时也可用卡片区的 **「✉ 发送邮件」** 补发那天的日报

`config.yaml` 的 `push.email` 只负责存 **SMTP 连接参数**，按下面三步配好即可。

### 1. 填写 SMTP 参数（`config.yaml` 的 `push.email`）

```yaml
push:
  email:                    # 只存 SMTP 连接参数；是否发邮件由 --email / 网页勾选控制
    smtp_host: smtp.qq.com  # 邮箱服务商的 SMTP 服务器
    smtp_port: 465          # SSL 用 465；STARTTLS 用 587
    use_ssl: true           # 465 → true；587 → false
    username: you@qq.com    # SMTP 登录账号（通常是完整邮箱）
    password: ""            # 授权码（不是登录密码）；建议留空走环境变量
    from_addr: you@qq.com   # 发件人；留空则自动用 username
    to_addrs:               # 收件人，可填多个
      - you@qq.com
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `smtp_host` | ✓ | 邮箱服务商的 SMTP 服务器地址 |
| `smtp_port` | ✓ | SSL 端口 `465` 或 STARTTLS 端口 `587` |
| `use_ssl` | ✓ | `true`=SSL(465)；`false`=STARTTLS(587) |
| `username` | ✓ | SMTP 登录账号，一般是完整邮箱 |
| `password` | ✓ | **授权码 / 应用专用密码**（不是邮箱登录密码！）建议放环境变量 |
| `from_addr` |  | 发件人；留空自动用 `username`（地址须与 `username` 一致，仅显示名可自定义） |
| `to_addrs` | ✓ | 收件人列表，可填多个 |

> 四个必填项缺任一个，运行时只打印「邮件配置不完整，跳过邮件发送」，不会报错中断。

### 2. 把授权码放进环境变量（推荐）

`password` 留空，改在 `.env`（已被 `.gitignore` 忽略）里设置；代码**优先读环境变量**，其次才用 `config.yaml` 的 `password`：

```bash
# .env
AI_HELP_ALL_SMTP_PASSWORD=你的授权码
```

### 3. 常见邮箱 SMTP 设置

| 邮箱 | `smtp_host` | 端口（`use_ssl`） | `password` 填什么 |
|---|---|---|---|
| QQ 邮箱 | `smtp.qq.com` | 465(true) / 587(false) | 设置→账户→开启 SMTP 后生成的**授权码** |
| 163 邮箱 | `smtp.163.com` | 465(true) | 开启 IMAP/SMTP 服务后的**授权码** |
| Gmail | `smtp.gmail.com` | 465(true) / 587(false) | 开启两步验证后的**应用专用密码** |
| Outlook / Office365 | `smtp.office365.com` | 587(false) | 账号密码或应用密码 |

> 多数邮箱默认禁用 SMTP，需先在邮箱设置里手动开启「IMAP/SMTP 服务」并生成授权码。

### 4. 测试

加 `--email` 跑一次，控制台出现 `[推送] 邮件已发送至 ...` 即成功；失败会打印原因（多为授权码错误，或端口与 `use_ssl` 不匹配）：

```bash
python main.py --email
```

## 定时每日运行（cron 示例）

```bash
# 每天早上 9:00 运行（请把路径换成你的实际路径）。要同时发邮件就在末尾加 --email
0 9 * * * cd /path/to/ai-help-all && python main.py --email >> run.log 2>&1
```

## 输出产物

- `digests/digest-YYYY-MM-DD.md`：markdown 日报。
- `digests/digest-YYYY-MM-DD.json`：结构化数据（供仪表盘 / 后续网站读取）。
- `digests/index.json`：历史日报索引。
- `digests/usage.json`：每日 token 用量账本（用于额度核对）。
