# ai-help-all

每天自动爬取 arxiv 新论文 → 用 LLM 按你的研究兴趣并发筛选打分 → 逐篇生成中文总结 → 推送（markdown / JSON / 邮件）。
另带一个**本地实时仪表盘网页**，可在 IDE 里边跑边看爬取 / 打分 / 总结的全过程。

## 功能流程

```
爬取 arxiv  →  历史去重  →  LLM 并发相关性打分(1-10)  →  阈值筛选  →  并发逐篇总结  →  推送
```

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

## 本地实时仪表盘（在 IDE 里看全过程）

```bash
python serve.py            # 默认 http://127.0.0.1:8000
```

启动后，在 Cursor / VSCode 命令面板运行 **“Simple Browser: Show”**，粘贴上面的地址，即可在编辑器内打开。
点「运行」后可实时看到：5 个阶段进度、打分进度条与实时打分流、入选论文卡片（总结生成中…会逐篇填充），还能用右上角下拉查看历史日报。

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

# 5. 正式跑：生成 digests/digest-YYYY-MM-DD.md（并按配置发邮件）
python main.py
```

> 若 `--list-models` 报连接/认证错误，多半是网络不可达或 api-key 问题。

## 配置要点（`config.yaml`）

- `arxiv.categories`：关注的 arxiv 分类，如 `cs.AI / cs.CL / cs.LG`。
- `arxiv.days_back`：往前看几天（平时 1，周一可设 3 把周末补上）。
- `interests`：用自然语言写清你的研究方向，**越具体筛得越准**。
- `relevance_threshold`：相关性阈值（1-10），达标才会被总结推送。
- `max_summarize`：每天最多总结几篇。
- `llm.filter_model / summarize_model`：可选模型见下表。
- `llm.requests_per_minute / tokens_per_minute`：限速（额度每分钟 10 次 / 10 万 token，默认留余量）。
- `push.email`：可选邮件推送，默认关闭。

## 可用模型（SJTU 交我算 API）

| 调用名 | 模型 | 上下文 | 侧重 |
|---|---|---|---|
| `glm-5.1` | GLM-5.1 | 128k | 强；直接返回内容，适合批量结构化打分（**默认筛选模型**） |
| `minimax-m2.7` | MiniMax-M2.7 | 192k | 强；**思考模型**，总结质量高（**默认总结模型**，需较大 `max_tokens`） |
| `deepseek-chat` | DeepSeek V3.2 常规 | 32k | 通用文本 |
| `deepseek-reasoner` | DeepSeek V3.2 思考 | 32k | 复杂推理（**不接受 temperature**） |
| `qwen3.5-27b` | Qwen3.5-27B | 256k | 最快、上下文最大，备选筛选模型 |

> 用 `python main.py --list-models` 可查看 api-key 实际可调用的模型 id。
>
> **关于思考模型**：`minimax-m2.7` / `deepseek-reasoner` 会先消耗 token 做内部推理，再输出答案。
> 若把它们用于**批量筛选**这类长 prompt 任务，`max_tokens` 不够时推理会吃光预算导致返回空，
> 因此筛选默认用直接返回内容、更快的 `glm-5.1`；总结篇幅短、用 `minimax-m2.7` 质量更佳。

## 定时每日运行（cron 示例）

```bash
# 每天早上 9:00 运行（请把路径换成你的实际路径）
0 9 * * * cd /path/to/ai-help-all && /path/to/python main.py >> run.log 2>&1
```

## 并发与速率限制

申请额度：每分钟 10 次请求 / 每分钟 100000 token / 每周 10 亿 token。
- **并发**：筛选(各批次)与总结(各论文)都用线程池并发提交（`llm.max_concurrency`，默认 8）。
  因为单次 LLM 调用的网络/生成延迟远大于限速间隔，并发能把这些延迟重叠起来，实测较串行快数倍。
- **双限速器**：`llm_client.py` 内置滑动窗口限速器，同时约束每分钟请求数与 token 消耗，
  并发再高也不会超额度（超了会自动等待）。
- 筛选阶段把多篇论文打包进一次请求（`filter_batch_size`，默认 20 篇/次）以省请求数。
- 每个请求都带 `max_tokens`，由 `filter_max_tokens / summarize_max_tokens` 控制。
- DeepSeek V3.2 要求请求必须含 `user` 消息（本项目均满足）；`deepseek-reasoner` 会自动不传 `temperature`。

## 输出产物

- `digests/digest-YYYY-MM-DD.md`：markdown 日报。
- `digests/digest-YYYY-MM-DD.json`：结构化数据（供仪表盘 / 后续网站读取）。
- `digests/index.json`：历史日报索引。
