"""Agent-injectable tools.

Inject concrete instances into ``AgentContext`` at orchestrator
construction time; agents read them via ``ctx.prospectus_tool`` etc.
"""

from __future__ import annotations

from .ifind_tool import IFindTool
from .kb_tool import KBTool
from .prospectus_tool import ProspectusTool
from .web_tool import WebTool

__all__ = (
    "IFindTool",
    "KBTool",
    "ProspectusTool",
    "WebTool",
)
