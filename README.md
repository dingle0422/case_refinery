# Case Refinery Service

独立离线服务：周期性先从上游拉取全部 `khCode`，再按 `khCode` 拉取 case 回流数据，按正/负样本做 LLM 提炼，写入 LanceDB v2 collection `case_{khCode}`，供主推理服务 `page-know-how` 在线检索增强。

## 与主仓的关系

- 本服务与主仓 `page-know-how` **完全解耦**，独立进程/容器部署。
- 本服务不 import 主仓任何业务模块；需要复用的通用工具（LLM 客户端、retry 装饰器）以 **复制 + 精简** 方式放在 `vendor/`。
- 主仓的 `app.py` / `inference/` 后续会通过 LanceDB v2 `search` 读 `case_{khCode}` collection，不会反向调用本服务。

## 数据流

```
APScheduler tick (per khCode)
  -> upstream.fetch_all_kh_codes()
  -> upstream.fetch_cases(khCode)
  -> lancedb_client.list_existing(khCode)
  -> classifier (polarity + record_hash + question_hash)
  -> dedupe.decide() ── need_refine 子集 ──> refiner (LLM)
                    └─── skip / bump_attempts ─┐
                                               v
                                       lancedb_writer.apply
                                               |
                                               v
                                         LanceDB v2 collection
```

关键设计：
- LLM refine 仅对 dedupe 后真正需要写入或覆盖的子集触发，避免无效 token 消耗。
- `vector` 由客户端先基于 `questionContent` 计算 embedding，再随 upsert 一起写入 LanceDB。
- 同 `question_hash` 不同 `record_hash` 的旧版本：通过 tombstone 标记（`md_tombstoned_*`）软删，主仓召回时通过 `where` 过滤掉。
- 失踪 case（库内存在但本轮上游未返回）保留不删；上游报错/空返回整轮 abort，不污染库存。

## 去重 / 覆盖决策表

| 库内同 `record_hash` | 已存 `refine_status` | 本轮 refine | 动作 |
|---|---|---|---|
| 否 | — | 成功 | append (refined) + tombstone 同 question 旧版 |
| 否 | — | 失败 | append (raw_fallback) + tombstone 同 question 旧版 |
| 是 | refined | — | skip |
| 是 | raw_fallback | 成功 | merge_by_chunk_id 升级为 refined（doc_id 复用）|
| 是 | raw_fallback | 失败 | refine_attempts += 1（达到 `refine_max_attempts` 后停止重试）|

## 本地启动

```bash
# 从仓库根目录
pip install -r case_refinery/requirements.txt
uvicorn case_refinery.app:app --host 0.0.0.0 --port 8090 --reload
```

启动后：

```bash
curl http://127.0.0.1:8090/healthz
curl http://127.0.0.1:8090/status
# 触发单个 khCode
curl -X POST http://127.0.0.1:8090/trigger/KH1493204307733168128_20260519101916
# 触发全部已配置 khCode
curl -X POST http://127.0.0.1:8090/trigger
```

## HTTP 接口一览

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/healthz` | 存活探针 |
| `GET` | `/status` | 调度配置 + 上轮 `RunSummary` 快照 |
| `POST` | `/trigger` | 同步触发：先拉 `listAllKh` 再逐个处理，返回 summary 列表 |
| `POST` | `/trigger/{kh_code}` | 同步触发单个 khCode |

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CASE_REFINERY_UPSTREAM_BASE_URL` | `http://10.199.0.40:8080/kg-platform` | 上游 case 接口 base |
| `CASE_REFINERY_UPSTREAM_LIST_ALL_PATH` | `/api/kh/listAllKh` | 全量 khCode 接口路径 |
| `CASE_REFINERY_UPSTREAM_LIST_PATH` | `/api/kh/listCorpusByKhCode` | case 列表接口路径（入参 `khCode`） |
| `CASE_REFINERY_LANCEDB_BASE_URL` | `http://mlp.paas.dc.servyou-it.com/kh-lancedb` | LanceDB v2 base |
| `CASE_REFINERY_LANCEDB_API_KEY` | `` | LanceDB API key（空表示无鉴权）|
| `CASE_REFINERY_LANCEDB_TIMEOUT_S` | `60.0` | LanceDB 请求超时（秒） |
| `CASE_REFINERY_LANCEDB_MAX_RETRIES` | `2` | LanceDB 网络异常/5xx 最大重试次数 |
| `CASE_REFINERY_LANCEDB_RETRY_BACKOFF_S` | `0.5` | LanceDB 重试退避基数（秒，指数退避） |
| `CASE_REFINERY_LANCEDB_RETRY_BACKOFF_MAX_S` | `4.0` | LanceDB 单次退避上限（秒） |
| `CASE_REFINERY_KH_CODES` | `` | 可选保留项（当前全量调度不依赖） |
| `CASE_REFINERY_SCHEDULE_CRON_HOUR` | `0` | 每日固定触发小时（默认 0 点） |
| `CASE_REFINERY_SCHEDULE_CRON_MINUTE` | `0` | 每日固定触发分钟（默认 00 分） |
| `CASE_REFINERY_SCHEDULE_INTERVAL_HOURS` | `0` | 调试覆盖：按小时间隔触发；>0 时覆盖 cron |
| `CASE_REFINERY_SCHEDULE_INTERVAL_SECONDS` | `0` | 调试覆盖：按秒间隔触发；>0 时优先级最高 |
| `CASE_REFINERY_LLM_VENDOR` | `servyou` | LLM vendor |
| `CASE_REFINERY_LLM_MODEL` | `deepseek-v3.2-1163259bcc6c` | LLM model |
| `CASE_REFINERY_EMBEDDING_BASE_URL` | `http://mlp.paas.dc.servyou-it.com/qwen3-embedding/v1` | Embedding 服务 base URL |
| `CASE_REFINERY_EMBEDDING_PATH` | `/embeddings` | Embedding 接口路径 |
| `CASE_REFINERY_EMBEDDING_MODEL` | `qwen3-embedding` | Embedding 模型名 |
| `CASE_REFINERY_EMBEDDING_API_KEY` | `` | Embedding 鉴权 key（空表示无鉴权） |
| `CASE_REFINERY_EMBEDDING_TIMEOUT_SEC` | `10.0` | Embedding 请求超时（秒） |
| `CASE_REFINERY_EMBEDDING_TIMEOUT_S` | （兼容别名） | 旧变量名；未设置 `..._SEC` 时会回退读取 |
| `CASE_REFINERY_REFINE_MAX_ATTEMPTS` | `5` | raw_fallback 累计尝试上限 |
| `CASE_REFINERY_LOG_LEVEL` | `INFO` | 日志级别 |

## 目录结构

```
case_refinery/
├── README.md
├── requirements.txt
├── config.py              # 所有配置
├── app.py                 # FastAPI 入口 + lifespan
├── scheduler.py           # APScheduler 作业注册
├── api/routes.py          # HTTP 路由
├── pipeline/
│   ├── upstream.py        # 上游 case 接口
│   ├── classifier.py      # polarity 判定 + 哈希
│   ├── prompts.py         # refine prompt 模板
│   ├── refiner.py         # LLM refine 调用
│   ├── lancedb_client.py  # v2 HTTP 客户端
│   ├── dedupe.py          # 去重决策
│   └── runner.py          # 任务编排
├── vendor/                # 从主仓复制的依赖
│   ├── llm_client.py
│   └── utils_helpers.py
└── tests/
```

## 测试

```bash
cd case_refinery
pytest -q
```
