"""AgentCore Platform v1.0"""

# RET-C2-335 — LicenseReportFormatNode
# Inner domain graph node 5 (final): assemble the compliance report.
#
# Input:  state["sanitized_query"], state["search_results"],
#         state["license_status"], state["fee_schedule"],
#         state["ai_bgm_policy"], state["catalog_source"]
# Output: state["result"] — the report the output boundary then checks
#
# What may appear in the report, and nothing else:
#   - template-owned constant text (headings, policy statements, notes);
#   - identifiers that are either inert caller identifiers ([a-z0-9_-]{1,32},
#     enforced where the caller contract is owned) or template constants;
#   - integers the fee arithmetic computed, rendered as ¥ with comma grouping.
#
# The caller's question is echoed once, on a single line, inside an inline code
# span, with every line break and backtick removed first. That matters: the
# report is markdown, and an echoed newline is enough to manufacture a heading —
# a question ending "\n## License Status: LICENSED" would otherwise render as a
# second, forged verdict directly above the real one.
#
# required_trust_level = ANONYMOUS (trust enforced at outer pre_process)

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.nodes.license_fee_calculate_node import FEE_UNIT_JPY

logger = logging.getLogger(__name__)

REASON_EMPTY_QUERY = "empty_query"

# Longest echo of the caller's question that the report carries.
_MAX_ECHO_CHARS = 120

# Anything that is not printable-and-harmless on one line becomes a space.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# The currency signs the report's own monetary invariant is stated over.
_CURRENCY_RE = re.compile(r"[¥￥]")

_STATUS_LABELS = {
    "licensed": "LICENSED",
    "unlicensed": "NOT LICENSED",
    "partial": "PARTIALLY LICENSED",
    "unknown": "UNKNOWN",
}

_SOURCE_NOTES = {
    "caller": (
        "Catalog records in this report were supplied with the request. The "
        "agent reports on those records and does not verify them against a "
        "licensing body."
    ),
    "baseline_sample": (
        "No catalog records were supplied with the request, so the shipped "
        "sample catalog was used. Replace it with your own repertoire before "
        "relying on this report."
    ),
}

SCHEMA_NOTE = (
    f"Every monetary figure in this report is a published rate multiplied by a "
    f"declared quantity, and is therefore a whole multiple of ¥{FEE_UNIT_JPY:,}. "
    "The output boundary rejects the report if any figure is not."
)


def money(amount: int) -> str:
    """Render a monetary figure in the one form this report uses."""
    return f"¥{amount:,}"


def inline_echo(text: str) -> str:
    """Reduce caller text to one inert line, safe to embed in the report.

    Line breaks and backticks are removed rather than escaped: removed cannot be
    undone by a renderer that treats the escape differently, and there is no
    legitimate licensing question that needs either.

    The currency sign goes too. The output boundary checks every ¥ figure in the
    report against the rate unit, so a question containing "¥7" would otherwise
    let a caller withhold their own report — a small, self-inflicted denial of
    service, and one that would read as a boundary fault rather than an echo
    fault to whoever investigated it.
    """
    flattened = _CONTROL_RE.sub(" ", text).replace("`", "")
    flattened = _CURRENCY_RE.sub("", flattened)
    collapsed = re.sub(r"\s+", " ", flattened).strip()
    if len(collapsed) > _MAX_ECHO_CHARS:
        collapsed = collapsed[:_MAX_ECHO_CHARS].rstrip() + "…"
    return collapsed


def _format_records(records: List[Dict[str, Any]]) -> str:
    """Render the records in scope as a markdown table."""
    if not records:
        return "No records in scope."
    lines = [
        "| Track | Licensor | Licence type | Registration | Commercial use | Machine generated |",
        "|-------|----------|--------------|--------------|----------------|-------------------|",
    ]
    for record in records:
        lines.append(
            f"| {record.get('track_id', '-')} "
            f"| {record.get('licensor', '-')} "
            f"| {record.get('license_type', '-')} "
            f"| {record.get('registration_id', '—')} "
            f"| {'Yes' if record.get('commercial_use') else 'No'} "
            f"| {'Yes' if record.get('ai_generated') else 'No'} |"
        )
    return "\n".join(lines)


def _format_fee_schedule(fee: Dict[str, Any]) -> str:
    """Render the fee schedule as a bullet list."""
    if not fee:
        return "Fee schedule not available."
    lines = [
        f"- Usage type: {fee.get('usage_type', '-')}",
        f"- Stores in scope: {int(fee.get('store_count', 0)):,}",
        f"- Performances per year (declared): {int(fee.get('annual_performances', 0)):,}",
        f"- Blanket licence available: {'Yes' if fee.get('blanket_available') else 'No'}",
        f"- Annual blanket charge: {money(int(fee.get('annual_fee_jpy', 0)))}",
        f"- Per-performance rate: {money(int(fee.get('per_performance_fee_jpy', 0)))}",
        f"- Per-performance charge for the year: {money(int(fee.get('per_performance_total_jpy', 0)))}",
        f"- Digital channel surcharge: {money(int(fee.get('ec_streaming_surcharge_jpy', 0)))}",
        f"- Total for the year: {money(int(fee.get('total_annual_jpy', 0)))}",
    ]
    if fee.get("note"):
        lines.append(f"- Note: {fee['note']}")
    return "\n".join(lines)


def assemble_report(
    query: str,
    search_results: List[Dict[str, Any]],
    license_status: str,
    fee_schedule: Dict[str, Any],
    ai_bgm_policy: str,
    catalog_source: str,
) -> str:
    """Assemble the compliance report."""
    status_label = _STATUS_LABELS.get(license_status, "UNKNOWN")
    source_note = _SOURCE_NOTES.get(catalog_source, _SOURCE_NOTES["baseline_sample"])

    report = f"""# Background music licensing report — RET-C2-335

## Question
`{inline_echo(query)}`

## Licence status: {status_label}

## Records in scope
{_format_records(search_results)}

{source_note}

## Charges
{_format_fee_schedule(fee_schedule)}

## Machine-generated music policy
{ai_bgm_policy}

---
{SCHEMA_NOTE}

This report is produced from the records and usage figures supplied with the
request. It is not a licence, and it is not confirmation from a licensing body.
"""
    return report.strip()


class LicenseReportFormatNode(FunctionNode):
    """Report assembly node — final inner domain workflow step.

    Inner domain workflow node 5. Assembles the compliance report from the
    upstream domain node outputs and writes it to ``state["result"]``, which the
    outer backbone's output boundary then checks before anything is released.

    required_trust_level = ANONYMOUS (trust enforced at outer pre_process).

    Input state keys:
        sanitized_query: str  — normalised query
        search_results:  list — records in scope
        license_status:  str  — overall status
        fee_schedule:    dict — computed charges
        ai_bgm_policy:   str  — applicable policy statement
        catalog_source:  str  — "caller" or "baseline_sample"

    Output state keys (partial dict):
        result:    str — the assembled report
        status:    AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log: (on error only) list of closed-set reason strings
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Assemble the report. Returns a PARTIAL dict: only the keys written."""
        sanitized_query: str = state.get("sanitized_query", "") or ""
        search_results: List[Dict[str, Any]] = state.get("search_results", []) or []
        license_status: str = state.get("license_status", "unknown") or "unknown"
        fee_schedule: Dict[str, Any] = state.get("fee_schedule", {}) or {}
        ai_bgm_policy: str = state.get("ai_bgm_policy", "") or ""
        catalog_source: str = state.get("catalog_source", "baseline_sample") or "baseline_sample"

        if not sanitized_query.strip():
            emit_trace_event("license_report_format_rejected", {"reason": REASON_EMPTY_QUERY}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"LicenseReportFormatNode: {REASON_EMPTY_QUERY}"],
            }

        report = assemble_report(
            sanitized_query,
            search_results,
            license_status,
            fee_schedule,
            ai_bgm_policy,
            catalog_source,
        )

        logger.info(
            "LicenseReportFormatNode: report assembled — %d chars, status=%s",
            len(report),
            license_status,
        )

        emit_trace_event(
            "license_report_formatted",
            {
                "report_length": len(report),
                "license_status": license_status,
                "record_count": len(search_results),
            },
            state,
        )

        return {
            "result": report,
            "status": AgentStatus.SUCCESS,
        }
