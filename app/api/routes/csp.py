"""
POST /csp-report: where browsers send Content-Security-Policy violations
from www.yarikama.com, while its policy runs in Report-Only mode
(personal-website-front-end vercel.json). Each report becomes one log line, which
Loki keeps and Grafana shows, to see what the policy would block before it
is enforced.

Browsers send two formats: the older report-uri one
({"csp-report": {...}}, application/csp-report) and the Reporting API's
list of reports (application/reports+json). The body is untrusted: it is
size-capped, and only a few fields, cut short, reach the log.
"""

import json
from typing import Any

from fastapi import APIRouter, Request, Response
from loguru import logger
from prometheus_client import Counter

router = APIRouter()

MAX_BODY_BYTES = 16 * 1024
MAX_FIELD_CHARS = 200

REPORTS = Counter(
    "csp_reports_total",
    "Content-Security-Policy violation reports from the site, by directive.",
    ["directive"],
)


def _field(report: dict[str, Any], *names: str) -> str:
    for name in names:
        value = report.get(name)
        if value not in (None, ""):
            return str(value)[:MAX_FIELD_CHARS]
    return "?"


def violations(body: object) -> list[dict[str, Any]]:
    """The violation reports in either format; anything else is ignored."""
    if isinstance(body, dict) and isinstance(body.get("csp-report"), dict):
        return [body["csp-report"]]
    if isinstance(body, list):
        return [
            item["body"]
            for item in body
            if isinstance(item, dict)
            and item.get("type") == "csp-violation"
            and isinstance(item.get("body"), dict)
        ]
    return []


@router.post("/csp-report", status_code=204)
async def csp_report(request: Request) -> Response:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            return Response(status_code=413)
    try:
        parsed = json.loads(body)
    except ValueError:
        return Response(status_code=400)

    for report in violations(parsed)[:20]:
        directive = _field(
            report, "effectiveDirective", "effective-directive", "violated-directive"
        )
        directive = directive.split()[0][:40]
        REPORTS.labels(directive).inc()
        logger.warning(
            f"CSP {_field(report, 'disposition')}: {directive} blocked "
            f"{_field(report, 'blockedURL', 'blocked-uri')} on "
            f"{_field(report, 'documentURL', 'document-uri')} "
            f"({_field(report, 'sourceFile', 'source-file')}:"
            f"{_field(report, 'lineNumber', 'line-number')})"
        )
    return Response(status_code=204)
