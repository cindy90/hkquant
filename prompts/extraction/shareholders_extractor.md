---
role: shareholders_extractor
version: 1.0
last_updated: 2026-05-18
input_schema: list[ChunkPayload]
output_schema: ShareholdersResponse
---

# Role
Extract pre-IPO shareholder structure + last private round valuation.

# Required output (JSON)

```json
{
  "shareholders": [
    {
      "name": "...",
      "pct_pre_ipo": 0.215,
      "is_controlling": true,
      "is_pre_ipo_investor": false,
      "last_round_valuation_rmb": null,
      "last_round_date": null,
      "has_buyback_clause": false,
      "citation": {"page": 215, "chunk_id": "..."}
    }
  ],
  "pre_ipo_valuation_rmb": "8500000000.00",
  "last_round_date": "2023-06-15",
  "needs_review": false
}
```
