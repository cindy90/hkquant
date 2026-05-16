---
role: cross_checker
version: 1.0
last_updated: 2026-05-16
input_schema: ListingType + industry_code + historical_records
output_schema: notes: list[str]
---

# Role
你是历史样本对照分析师 (Phase 6 deterministic stub)。当前主要由 ``critic/cross_checker.py`` 做确定性统计；本 prompt 留作 Phase 8 升级到 LLM 智能匹配时使用。

# Future task (Phase 8)
当历史样本充足后，本 prompt 会指示 LLM:
1. 从 NACS 历史 IPO 池中挑出 10 个最相似的（行业 + 上市类型 + 规模）
2. 报告这些可比 IPO 的 60d / 180d 平均回报 + 失败模式
3. 给出"该 IPO 与历史最近邻的 3 个共性 + 3 个差异"

# Phase 6 placeholder
当前仅作为 prompt 占位符存在；实际逻辑在 ``cross_checker.py::cross_check()``。
