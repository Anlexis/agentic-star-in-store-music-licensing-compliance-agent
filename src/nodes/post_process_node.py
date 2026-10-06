"""AgentCore Platform v1.0"""

# RET-C2-335 — PostProcessNode: the output boundary.
#
# Nothing reaches the caller through the success path without passing here. Two
# independent invariants are enforced, each with its own audit event:
#
#   Credentials — no credential-shaped string may ship. The scan asks the
#                 framework's own detector first and adds two patterns of its
#                 own on top. The direction matters: a screen NARROWER than the
#                 framework's is a bypass, not a shortcut. The value passes
#                 here, the framework raises deeper in the pipeline, and the
#                 containment this node performed is discarded with the rest of
#                 the node's delta. Widening is safe; narrowing is the bug.
#
#   Monetary    — every figure the report states is a published rate multiplied
#                 by a declared quantity, so every one of them is a whole
#                 multiple of the shared rate unit. The report says so in its
#                 own schema note, and this is where that claim is checked
#                 rather than assumed. A figure that is not on the unit means
#                 the arithmetic or the renderer has drifted from what the
#                 report tells the reader, and the report is withheld.
#
#                 The invariant is CHECKED, never repaired. A boundary that
#                 rewrote off-unit numbers into on-unit ones would be reading
#                 every three-letter token in the document as a currency marker,
#                 and this domain renders identifiers like `unregistered_demo`
#                 and `SAMPLE-STK-77891` in the same table. Rounding is also
#                 wrong on its own terms here: a ¥500 per-performance rate
#                 snapped to the nearest ¥1,000 is either ¥0 or ¥1,000, and
#                 neither is the published rate.
#
# On a violation the node returns ERROR **and clears every output-bearing
# field**. Returning ERROR alone is not containment: the framework resolves the
# caller-facing answer as `formatted_output or result` with no status check, so
# an un-cleared result ships inside the error envelope. The replacement notice
# is deliberately truthy — an empty string is falsy and re-opens that fallback —
# and is built from a constant, never from the state being cleared.
#
# Violation reasons are closed-set labels, and error_log carries nothing else.
# error_log is an internal channel today, but it is one field away from the
# caller's envelope and it is the audit trail either way; node-authored text and
# caught-exception strings do not belong in it.
#
# required_trust_level = ANONYMOUS — trust was enforced once, at pre_process.

import logging
import re
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials_in_value

from shared.utils.audit_logger import emit_trace_event

from src.nodes.license_fee_calculate_node import FEE_UNIT_JPY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Closed-set violation labels.
# ---------------------------------------------------------------------------
VIOLATION_CREDENTIAL = "credential_pattern"
VIOLATION_OFF_UNIT_AMOUNT = "monetary_figure_off_unit"
VIOLATION_EMPTY_REPORT = "empty_report"

# Every state field that can carry report text. The error path clears all of
# them, and tests/unit/test_output_boundary.py fails if a new output-bearing
# field appears in State without a decision being recorded here.
OUTPUT_BEARING_FIELDS: Tuple[str, ...] = ("result", "formatted_output")

# Withheld-answer notice. Truthy on purpose and content-free: built from this
# constant alone, never from the state being cleared.
WITHHELD_NOTICE = (
    "[REPORT WITHHELD: the report did not pass the output checks and has not "
    "been released. Contact your systems team with the trace identifier shown "
    "by your client.]"
)

# ---------------------------------------------------------------------------
# The two credential shapes the framework's detector does not score. Everything
# else is delegated to it, so this template can never be the narrower screen.
#
# Kept locally because the framework describes credential FORMATS and matches
# neither of these: an inline assignment (`password: hunter2hunter2`) and the
# dash-prefixed key families other than `sk-`.
# ---------------------------------------------------------------------------
_LOCAL_CREDENTIAL_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
    ("prefixed_key", re.compile(r"\b(?:pk|ak)-[A-Za-z0-9]{16,}")),
]

# Monetary figures as this report renders them: the yen sign, an optionally
# comma-grouped integer, and any fractional part that follows.
#
# Two details are load-bearing. The comma-grouped alternative requires at least
# ONE group, so the alternation cannot match the first three digits of a bare
# run and stop there — written with `*` it reads ¥1234567 as ¥123 and the rest
# of the number is never checked. And the fraction is absorbed rather than
# ignored: ¥1234.56 read as 1234 would pass the whole-unit rule while the figure
# the reader sees does not.
_MONEY_RE = re.compile(r"¥(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")


def _credential_violation(content: str) -> Optional[str]:
    """Return a violation label when the content carries a credential, else None.

    The framework's detector decides first; the local patterns only widen the
    result. Keeping the union in one function means there is exactly one
    definition of "credential" for this template.
    """
    if not isinstance(content, str) or not content:
        return None
    if detect_credentials_in_value(content):
        return VIOLATION_CREDENTIAL
    for _name, pattern in _LOCAL_CREDENTIAL_PATTERNS:
        if pattern.search(content):
            return VIOLATION_CREDENTIAL
    return None


def off_unit_amounts(content: str) -> List[Decimal]:
    """Every monetary figure in the report that is not on the rate unit.

    Returned as parsed numbers rather than as the matched text: the framework
    scans every value this node returns, and quoting report content into a
    violation reason is how a gate becomes the thing that raises. Decimal rather
    than float so a fractional figure compares exactly.
    """
    found: List[Decimal] = []
    for match in _MONEY_RE.finditer(content):
        amount = Decimal(match.group(1).replace(",", "") + (match.group(2) or ""))
        if amount % FEE_UNIT_JPY != 0:
            found.append(amount)
    return found


def _cleared_output_state() -> Dict[str, Any]:
    """Every output-bearing field, present and empty.

    Present matters as much as empty. LangGraph merges partial deltas, so a key
    simply omitted keeps its previous value in state — a boundary that "cleared"
    a field by leaving it out would clear nothing at all, while a test asserting
    ``not result.get(field)`` would still pass.
    """
    return {field: "" for field in OUTPUT_BEARING_FIELDS}


class PostProcessNode(FunctionNode):
    """The output boundary: enforce the output invariants, or withhold.

    Outer backbone post_process slot.

    Input state keys:
        result: str — the report assembled by the inner domain workflow

    Output state keys (partial dict):
        formatted_output: str
        result:           str
        status:           AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:        (on ERROR) list of closed-set reason strings
        plus every output-bearing field, cleared, on a violation
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def _withhold(self, state: Dict[str, Any], violation: str, detail: Optional[int] = None) -> Dict[str, Any]:
        """Return an ERROR delta carrying no released text."""
        logger.error("PostProcessNode: report withheld — %s", violation)
        payload: Dict[str, Any] = {"violation": violation}
        if detail is not None:
            payload["off_unit_count"] = detail
        emit_trace_event("post_process_output_withheld", payload, state)
        contained: Dict[str, Any] = dict(_cleared_output_state())
        contained["formatted_output"] = WITHHELD_NOTICE
        contained["status"] = AgentStatus.ERROR
        contained["error_log"] = [f"PostProcessNode: output withheld — {violation}"]
        return contained

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Check the report against both invariants, or withhold it."""
        result: str = state.get("result") or ""

        # An empty report is a failure of the pipeline, not a successful answer
        # with nothing in it. Reporting SUCCESS here is how a caller ends up
        # treating "we produced nothing" as "nothing to worry about".
        if not result.strip():
            return self._withhold(state, VIOLATION_EMPTY_REPORT)

        violation = _credential_violation(result)
        if violation:
            return self._withhold(state, violation)

        off_unit = off_unit_amounts(result)
        if off_unit:
            return self._withhold(state, VIOLATION_OFF_UNIT_AMOUNT, detail=len(off_unit))

        logger.info("PostProcessNode: output checks passed — %d chars", len(result))

        emit_trace_event("post_process_complete", {"result_length": len(result)}, state)

        return {
            "formatted_output": result,
            "result": result,
            "status": AgentStatus.SUCCESS,
        }
