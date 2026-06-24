# ai-help-all

每天自动爬取 arxiv 新论文 → 用 LLM 按你的研究兴趣筛选打分 → 逐篇生成中文总结 → 推送给你（markdown 日报 / 邮件）。

## 功能流程

```
爬取 arxiv  →  历史去重  →  LLM 相关性打分(1-10)  →  阈值筛选  →  逐篇总结  →  推送
```

## 目录结构

```
ai-help-all/
├── main.py                 # 主流程 / CLI 入口
├── ai_help_all/
│   ├── config.py          # 配置加载（密钥优先从环境变量取）
│   ├── arxiv_crawler.py    # arxiv 爬取
│   ├── llm_client.py       # OpenAI 兼容客户端 + 限速 + 重试
│   ├── filter.py           # LLM 批量相关性打分 + 阈值筛选
│   ├── summarizer.py       # 逐篇中文结构化总结
│   ├── seen.py             # 已推送论文去重
│   └── push.py             # markdown 日报 + 邮件推送
├── config.example.yaml     # 配置示例（复制为 config.yaml 后使用）
├── .env.example            # 密钥示例（复制为 .env 后使用）
├── requirements.txt
└── .gitignore              # 已忽略 config.yaml / .env / digests / seen_papers.json
```

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
| `glm-5.1` | GLM-5.1 | 128k | 代码与长程任务（最强，**默认总结模型**） |
| `minimax-m2.7` | MiniMax-M2.7 | 192k | 智能体任务/长上下文（**默认筛选模型**，适合批量塞摘要） |
| `deepseek-chat` | DeepSeek V3.2 常规 | 32k | 通用文本 |
| `deepseek-reasoner` | DeepSeek V3.2 思考 | 32k | 复杂推理（**不接受 temperature**，会消耗大量思考 token） |
| `qwen3.5-27b` | Qwen3.5-27B | 256k | 长上下文/多模态 |

> 用 `python main.py --list-models` 可查看 api-key 实际可调用的模型 id。

## 定时每日运行（cron 示例）

```bash
# 每天早上 9:00 运行（请把路径换成你的实际路径）
0 9 * * * cd /path/to/ai-help-all && /path/to/python main.py >> run.log 2>&1
```

## 速率限制与注意事项

申请额度：每分钟 10 次请求 / 每分钟 100000 token / 每周 10 亿 token。
- `llm_client.py` 内置**滑动窗口双限速器**，同时约束每分钟请求数与 token 消耗，超限会自动等待。
- 筛选阶段把多篇论文打包进一次请求（`filter_batch_size`，默认 20 篇/次）以省请求数。
- 每个请求都会带 `max_tokens`；筛选/总结输出上限分别由 `filter_max_tokens / summarize_max_tokens` 控制。
- DeepSeek V3.2 要求请求必须含 `user` 消息（本项目均满足）；选用 `deepseek-reasoner` 时会自动不传 `temperature`。
