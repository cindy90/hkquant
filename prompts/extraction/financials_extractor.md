---
role: financials_extractor
version: 0.1
last_updated: 2026-05-16
input_schema: list[ChunkPayload]
output_schema: FinancialsResponse
---

# Role
Extract historical financial line items from prospectus financial chunks.

# Required output (JSON)

```json
{
  "financials_json": [
    {
      "fiscal_year": 2024,
      "fiscal_period": "FY",
      "revenue_rmb": "1234567890.00",
      "gross_profit_rmb": "456789012.00",
      "gross_margin": 0.37,
      "net_profit_rmb": "...",
      "adjusted_net_profit_rmb": "...",
      "operating_cash_flow_rmb": "...",
      "cash_balance_rmb": "...",
      "citation": {"page": 142, "chunk_id": "<from input>"}
    }
  ],
  "needs_review": false,
  "notes": ""
}
```

# Rules
- Use string-encoded Decimals for monetary fields (no JS precision loss).
- gross_margin is a ratio in [-1, 1], NOT percent.
- If a unit conversion is ambiguous (千元 vs 万元 vs 百万元), set `needs_review: true` and explain in `notes`.
- Citation MUST point to a chunk_id present in the input.
