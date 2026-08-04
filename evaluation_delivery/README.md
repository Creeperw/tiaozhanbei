# evaluation_delivery —— 中医个性化复习系统测评环境（可移交）

本目录为比赛测评工作提供一套**自包含、可移交**的评测框架：进程内驱动真实
模型链路（live 模式），按用例跑完落盘 JSONL，双通道判定（规则 + LLM Judge），
输出带 Wilson 95% CI 的 Markdown 报告。

> 评测工作交给其他人：拿到本目录 + 后端仓库 + API keys 即可独立运行，无需
> 了解框架内部实现。

---

## 1. 目录结构

```
evaluation_delivery/
├── runner.py        # 进程内评测运行器（容器构建 / 用例执行 / JSONL 落盘 / 断点续跑）
├── judges.py        # 判定通道：规则比对、LLM Judge（DeepSeek）、Embedding（SiliconFlow）
├── stats.py         # Wilson 95% CI、比例渲染、报告写入
├── codex1_hallucination.py   # 方案01：专业知识谬误率（幻觉率）
├── codex2_adaptation.py      # 方案02：画像与资源难度适配度
├── codex3_coverage.py        # 方案03：知识点覆盖率
├── eval.env.example          # 评测环境变量模板（复制为 eval.env 后填写）
├── sample_cases/             # 冒烟样例（小批量验证框架可用）
└── outputs/                  # 运行产物（JSONL + 报告 + 快照，git 忽略）
```

## 2. 环境配置

复制 `eval.env.example` 为 `eval.env` 并填写：

```bash
cp eval.env.example eval.env
# 编辑 eval.env：填入真实 API keys
```

评测框架的模型链路使用后端 `competition_app/.env.local` 中的配置
（CHAT_BASE_URL / CHAT_MODELS / DASHSCOPE_API_KEY / SILICONFLOW_API_KEY）。
`eval.env` 只配置**判定通道**自己的密钥：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `EVAL_JUDGE_BASE_URL` | LLM Judge API 地址 | `https://api.deepseek.com` |
| `EVAL_JUDGE_API_KEY` | LLM Judge API key（必填） | — |
| `EVAL_JUDGE_MODEL` | Judge 模型 | `deepseek-v4-flash` |
| `EVAL_EMBED_BASE_URL` | Embedding API 地址 | `https://api.siliconflow.cn/v1` |
| `EVAL_EMBED_API_KEY` | Embedding API key（覆盖率通道 A 需要） | — |
| `EVAL_EMBED_MODEL` | Embedding 模型 | `Qwen/Qwen3-Embedding-4B` |

## 3. 用例格式

每个 case 是一个 dict，最少含：

```python
{
    "case_id": "唯一标识（断点续跑依据）",
    "user_request": "发给系统的用户请求",
    "expected": {...},        # 可选：期望答案/黄金标签
    "meta": {...},            # 可选：附加信息，原样透传落盘
}
```

## 4. 运行

```bash
# 冒烟（小批量验证）
python codex1_hallucination.py --limit 3 --env eval.env

# 方案01 幻觉率（默认抽样 1000 题，按文件分层）
python codex1_hallucination.py --env eval.env

# 方案02 适配度（默认 270 组：9 画像 × 10 人 × 3 知识点）
python codex2_adaptation.py --limit 5 --env eval.env
python codex2_adaptation.py --env eval.env

# 方案03 覆盖率（先核验 kp_id 体系，再跑全量 200 题）
python codex3_coverage.py --verify --env eval.env
python codex3_coverage.py --limit 10 --env eval.env
python codex3_coverage.py --env eval.env
```

通用参数：`--limit` 小批量冒烟、`--workers` 并发数（默认 3）、`--run-tag`
运行标识（默认时间戳）、`--env` 判定通道 env 文件。

## 5. 输出

```
outputs/
├── hallucination_<ts>.jsonl      # 原始结果（每 case 一行）
├── hallucination_report_<ts>.md  # 汇总报告（模型级/系统级幻觉率 + CI + 类型分布）
├── hallucination_failures_<ts>.md# 失败案例清单（可回放追责）
├── adaptation_<ts>.jsonl / adaptation_report_<ts>.md
├── coverage_<ts>.jsonl / coverage_report_<ts>.md
└── snapshots/                    # 框架快照（执行轨迹，审计用）
```

**断点续跑**：同 run_name 已完成的 case 自动跳过。中断后重跑同一条命令即可。

## 6. 判定口径

- **通道 A（规则）**：客观题选项字母比对、难度区间、embedding 余弦阈值
- **通道 B（LLM Judge）**：DeepSeek 直连，CoT + 冻结 prompt 版本（`JUDGE_PROMPT_VERSION`）
- A/B 冲突的 case 在报告中标 "待人工复核"，JSONL 中 `conflict=true`

## 7. 已知约束（评测前必读）

1. **题库 answer 格式为列表** `['C']`，框架已适配。
2. **题库 kp_ids 几乎为空**（6236 题中仅 17 题有值）——覆盖率脚本按
   **题干/选项/解释全文关键词**匹配黄金知识点；若命中率低，请按比赛大纲
   补充黄金题数据（`GOLD_KPS` 已在 codex3 顶部，可替换）。
3. **ResourceDraft 暂无 difficulty 字段**——适配度客观通道会尝试从输出
   文本提取"难度:N"，提取不到则自动降级为仅主观通道并在报告中标注。
4. **数据对照源**（CMHE 等公开数据集）工作区未提供，如需对照请自行准备。
5. 全量运行耗时长（每题约 1-3 分钟），建议 `--limit` 冒烟后分批跑。

## 8. 常见问题

| 问题 | 处理 |
|---|---|
| `EVAL_JUDGE_API_KEY 未配置` | 填 eval.env 并 `--env eval.env` |
| `评测需要 live 模式` | 检查 backend `competition_app/.env.local` 中 COMPETITION_APP_MODE=live 且 API keys 完整 |
| 题库目录为空 | 确认 backend 仓库已同步（competition_app/data/qualification_papers/papers/） |
| 覆盖率 0% | 运行 `--verify` 看核验报告；黄金 kp 名与题库措辞不匹配，需补黄金题 |
| 全部 failed | 看 JSONL 的 error_message；多为模型 key 失效或超时，重试即可 |
