"""18A biotech valuation specialization — pipeline-probability NPV.

Not implemented in Phase 4. Spec §3.7 requires "至少先实现 AI/SaaS 和半导体",
so this file is a placeholder for future work.

Planned approach (Phase 5+ when biotech agent is implemented):
- Model each drug candidate pipeline stage (Pre-clinical → Phase I/II/III → NDA)
- Assign stage-conditional success probabilities from FDA/NMPA historical rates
- NPV each candidate: revenue_if_approved × P(success) / (1 + WACC)^years_to_market
- Sum across pipeline → equity value distribution via Monte Carlo

This complements ``milestones.py`` which handles 18C-PRE_COMMERCIAL generically;
biotech_18a would add domain-specific pipeline probability data.
"""
