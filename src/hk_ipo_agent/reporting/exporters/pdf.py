"""PDF exporter — renders the investment memo to PDF via WeasyPrint.

Phase 7 MVP: takes markdown from ``report_builder.build_memo_markdown``,
wraps it in minimal HTML, runs through WeasyPrint, returns PDF bytes.

WeasyPrint depends on system libs (cairo / pango). On import failure we
fall back to returning the HTML bytes so unit tests don't require the
native deps; production callers should have WeasyPrint working.
"""

from __future__ import annotations

from typing import Any

from ...common.schemas import PredictionSnapshot
from ..report_builder import build_memo_markdown

_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"/>
<title>Investment Memo — {company}</title>
<style>
  body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif; max-width: 720px; margin: 24px auto; color: #1d1d1f; }}
  h1, h2, h3 {{ color: #003366; }}
  h1 {{ border-bottom: 2px solid #003366; padding-bottom: 6px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
  code {{ background: #f5f5f7; padding: 2px 4px; border-radius: 3px; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 18px 0; }}
</style>
</head><body>
{body}
</body></html>"""


def _markdown_to_html(md: str) -> str:
    """Light markdown → HTML conversion sufficient for the memo template."""
    try:
        import markdown as md_lib

        return md_lib.markdown(  # type: ignore[no-any-return]
            md,
            extensions=["tables", "fenced_code"],
            output_format="html5",
        )
    except ImportError:
        return f"<pre>{md}</pre>"


def export_pdf(snapshot: PredictionSnapshot) -> bytes:
    """Render the memo to PDF bytes.

    Falls back to HTML bytes if WeasyPrint native libs are missing.
    """
    md = build_memo_markdown(snapshot)
    body_html = _markdown_to_html(md)
    company = snapshot.input_data_snapshot.get("extraction", {}).get("company_name_zh", "(unknown)")
    html = _HTML_TEMPLATE.format(company=company, body=body_html)

    try:
        from weasyprint import HTML

        weasy_html: Any = HTML(string=html)
        pdf: bytes | None = weasy_html.write_pdf()
        return pdf if pdf is not None else html.encode("utf-8")
    except Exception:
        return html.encode("utf-8")


__all__ = ("export_pdf",)
