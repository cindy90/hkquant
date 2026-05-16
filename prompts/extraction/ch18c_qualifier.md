---
role: ch18c_qualifier
version: 0.1
last_updated: 2026-05-16
input_schema: list[ChunkPayload]
output_schema: Ch18CResponse
---

# Role
Decide Chapter 18C qualification fields (specialty technology rule).

# Required output (JSON)

```json
{
  "is_commercialized": true,
  "revenue_threshold_met": true,
  "rd_intensity_met": true,
  "market_cap_threshold_hkd": "6000000000",
  "lead_investors": ["<领航投资人 1>", "<领航投资人 2>"]
}
```

# Reference thresholds (2024-09-01+)
- Commercialized: market cap >= HKD 4B, revenue >= RMB 250M
- Pre-commercial: market cap >= HKD 8B, R&D intensity per HKEx 18C.05

(Pre-2024-09-01 thresholds are higher — see `config/regulations/ch18c_pre_20240901.yaml`.)
