"""从主仓 page-know-how 复制 + 精简的通用工具。

本目录的代码独立演化，**不与主仓同步**。复制源：

- ``llm_client.py``  <- ``page-know-how/llm/client.py``（剥离 verbose_logger 依赖）
- ``utils_helpers.py`` <- ``page-know-how/utils/helpers.py``（仅保留 retry 装饰器）

复制时间：2026-05-27
"""
