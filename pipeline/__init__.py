"""case_refinery 业务流水线。

模块拆分原则：单向依赖、易 mock。每个模块只关心自己的一段职责：

- :mod:`.upstream`        : 上游 case 接口客户端
- :mod:`.classifier`      : polarity 判定 + record_hash / question_hash
- :mod:`.prompts`         : 正/负样本两套 refine prompt 模板
- :mod:`.refiner`         : 调用 LLM 做提炼，输出 refined_knowledge 或 raw_fallback
- :mod:`.embedder`        : 对 ``questionContent`` 做 embedding（客户端预计算向量）
- :mod:`.lancedb_client`  : LanceDB v2 HTTP 客户端（list / upsert / tombstone）
- :mod:`.dedupe`          : 去重 + 覆盖决策表
- :mod:`.runner`          : 一轮完整任务的编排
"""
