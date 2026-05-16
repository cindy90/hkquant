---
role: risks_extractor
version: 0.1
last_updated: 2026-05-16
input_schema: list[ChunkPayload]
output_schema: RisksResponse
---

# Role
Extract risk factors with category + severity.

# Required output (JSON)

```json
{
  "risk_factors": [
    {
      "category": "business | industry | financial | regulatory | macro | structural",
      "description": "<verbatim or close paraphrase>",
      "severity": "high | medium | low",
      "citation": {"page": 80, "chunk_id": "..."}
    }
  ],
  "needs_review": false
}
```
