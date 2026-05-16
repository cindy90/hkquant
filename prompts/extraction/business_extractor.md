---
role: business_extractor
version: 0.1
last_updated: 2026-05-16
input_schema: list[ChunkPayload]
output_schema: BusinessResponse
---

# Role
Extract business model + revenue streams + customer/supplier concentration.

# Required output (JSON)

```json
{
  "business_model": "<1-3 sentence summary>",
  "revenue_streams": [
    {"name": "...", "fiscal_year": 2024, "amount_rmb": "...", "pct": 0.45}
  ],
  "customer_concentration": [
    {"fiscal_year": 2024, "top1_pct": 0.32, "top5_pct": 0.61,
     "top1_name": "...", "citation": {"page": 200, "chunk_id": "..."}}
  ],
  "needs_review": false
}
```
