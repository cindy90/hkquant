"""API routers (Phase 7 → 11.2).

Implemented:
- Phase 7 MVP: health / dashboard / auth / ipos / snapshots / analysis /
  prospectus / whatif / alerts / audit / chat
- Phase 7.5b: reviews / proposals / drift
- Phase 8d: backtest
- Phase 11.2: upload (PDF prospectus upload + pipeline trigger)

Stubs (settings is Phase 9; system returns dev defaults):
- settings / system
"""

from __future__ import annotations

from .alerts import router as alerts_router
from .analysis import router as analysis_router
from .audit import router as audit_router
from .auth import router as auth_router
from .backtest import router as backtest_router
from .chat import router as chat_router
from .dashboard import router as dashboard_router
from .drift import router as drift_router
from .health import router as health_router
from .ipos import router as ipos_router
from .proposals import router as proposals_router
from .prospectus import router as prospectus_router
from .reviews import router as reviews_router
from .settings import router as settings_router
from .snapshots import outcomes_router
from .snapshots import router as snapshots_router
from .system import router as system_router
from .upload import router as upload_router
from .whatif import router as whatif_router

# All routers in the order to mount. Health first so /health survives any
# auth misconfiguration.
ALL_ROUTERS = (
    health_router,
    auth_router,
    dashboard_router,
    ipos_router,
    snapshots_router,
    analysis_router,
    prospectus_router,
    upload_router,
    outcomes_router,
    whatif_router,
    alerts_router,
    audit_router,
    chat_router,
    # Phase 7.5 / 8 deferred:
    backtest_router,
    drift_router,
    proposals_router,
    reviews_router,
    settings_router,
    system_router,
)

__all__ = ("ALL_ROUTERS",)
