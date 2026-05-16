# ADR 0004: Use LlamaParse primary + PyMuPDF fallback for prospectus PDF parsing

- **Status**: Accepted
- **Date**: 2026-05-16
- **Deciders**: project lead

## Context

Required by PROJECT_SPEC.md §1 technology stack.

## Decision

Use LlamaParse primary + PyMuPDF fallback for prospectus PDF parsing.

## Consequences

### Positive
- LlamaParse 表格识别质量优于 PyMuPDF 纯文本提取，对财务报表等结构化内容尤其重要
- PyMuPDF 作为 fallback 确保无外部 API 依赖时仍可运行（CI / 离线环境）
- 两路径产出相同 `ParsedDocument` 结构，下游完全透明

### Negative
- LlamaParse 需要 `LLAMA_CLOUD_API_KEY`，增加运行时依赖
- LlamaParse 存在延迟 + 费用，大批量处理需要考虑成本
- 双路径维护成本：table extraction 质量在 PyMuPDF fallback 下降级

### Neutral
- Camelot / Tabula 表格增强作为 Phase 3.1 备选（`ParserBackend.PYMUPDF_PLUS_CAMELOT` 枚举已预留）
- Phase 9 端到端 golden test 将对比两路径输出一致性
