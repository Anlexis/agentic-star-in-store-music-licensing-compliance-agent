"""AgentCore Platform v1.0"""

# RET-C2-335 — LicenseFeeCalculateNode
# Inner domain graph node 3: work out what the queried usage costs.
#
# Input:  state["license_status"], state["search_results"],
#         state["validated_context"]  — the caller's usage profile
# Output: state["fee_schedule"]
#
# The arithmetic is deliberately simple and deliberately real: a blanket annual
# charge per store, an optional surcharge per store for digital channels, and a
# per-performance charge multiplied by the declared volume over twelve months.
# An earlier version returned the same three constants for every request, which
# meant the report read the same for one store and for a thousand — a number
# that does not move with its input is a decoration, not an answer.
#
# Every rate below is a whole multiple of _FEE_UNIT_JPY, and every rendered
# figure is a rate times a bounded integer. That is what makes the output
# boundary's monetary invariant checkable rather than aspirational.
#
# required_trust_level = ANONYMOUS (trust enforced at outer pre_process)

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.services.input_guard import default_profile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Published tariff constants. Sample rates: replace them with the schedule your
# own licensing bodies publish. Every one is a whole multiple of _FEE_UNIT_JPY,
# and the output boundary checks that property on the rendered report.
# ---------------------------------------------------------------------------

# The granularity every published rate shares. The output boundary enforces it
# on every monetary figure that reaches the caller.
FEE_UNIT_JPY = 100

# Blanket annual licence, per store, physical retail.
BLANKET_ANNUAL_PER_STORE_JPY = 6_000

# Per-performance charge for usage outside a blanket licence.
PER_PERFORMANCE_JPY = 500

# Surcharge per store for digital/streaming channels, on top of the blanket.
EC_STREAMING_SURCHARGE_PER_STORE_JPY = 3_000

# Per-performance charge for AI-generated background music, for which no
# blanket licence is offered.
AI_PER_PERFORMANCE_JPY = 800

_MONTHS_PER_YEAR = 12

USAGE_UNLICENSED = "unlicensed"
USAGE_AI_GENERATED = "ai_generated_bgm"
USAGE_EC_STREAMING = "ec_streaming"
USAGE_PHYSICAL_RETAIL = "physical_retail"

NOTE_UNLICENSED = (
    "No licence covers the records in scope. A separate agreement is required "
    "before this music is played commercially."
)
NOTE_AI_GENERATED = (
    "Per-performance charging applies. No blanket licence is offered for "
    "AI-generated music, so the annual figure is zero by construction."
)
NOTE_BLANKET = (
    "A blanket licence covers the records in scope. The annual figure is the "
    "per-store rate multiplied by the declared store count."
)

REASON_EMPTY_QUERY = "empty_query"


def compute_fee_schedule(
    license_status: str,
    search_results: List[Dict[str, Any]],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute the fee schedule for the declared usage.

    Reads the usage profile — store count, monthly performance volume and
    channel — rather than guessing them out of the query text. Keyword-sniffing
    the question was how the previous version decided whether a shop was a
    website: it matched the word "online" anywhere in a sentence, including in
    "is the online catalogue authoritative".
    """
    store_count = int(profile.get("store_count", 1))
    monthly = int(profile.get("monthly_performances", 0))
    channel = str(profile.get("channel", USAGE_PHYSICAL_RETAIL))
    is_digital = channel in ("ec_streaming", "both")
    is_ai = any(bool(record.get("ai_generated")) for record in search_results)

    annual_performances = monthly * _MONTHS_PER_YEAR

    if license_status == USAGE_UNLICENSED:
        return {
            "usage_type": USAGE_UNLICENSED,
            "store_count": store_count,
            "annual_performances": annual_performances,
            "annual_fee_jpy": 0,
            "blanket_available": False,
            "per_performance_fee_jpy": 0,
            "per_performance_total_jpy": 0,
            "ec_streaming_surcharge_jpy": 0,
            "total_annual_jpy": 0,
            "note": NOTE_UNLICENSED,
        }

    if is_ai:
        surcharge = EC_STREAMING_SURCHARGE_PER_STORE_JPY * store_count if is_digital else 0
        per_performance_total = AI_PER_PERFORMANCE_JPY * annual_performances
        return {
            "usage_type": USAGE_AI_GENERATED,
            "store_count": store_count,
            "annual_performances": annual_performances,
            "annual_fee_jpy": 0,
            "blanket_available": False,
            "per_performance_fee_jpy": AI_PER_PERFORMANCE_JPY,
            "per_performance_total_jpy": per_performance_total,
            "ec_streaming_surcharge_jpy": surcharge,
            "total_annual_jpy": per_performance_total + surcharge,
            "note": NOTE_AI_GENERATED,
        }

    surcharge = EC_STREAMING_SURCHARGE_PER_STORE_JPY * store_count if is_digital else 0
    annual_fee = BLANKET_ANNUAL_PER_STORE_JPY * store_count
    return {
        "usage_type": USAGE_EC_STREAMING if is_digital else USAGE_PHYSICAL_RETAIL,
        "store_count": store_count,
        "annual_performances": annual_performances,
        "annual_fee_jpy": annual_fee,
        "blanket_available": True,
        "per_performance_fee_jpy": PER_PERFORMANCE_JPY,
        # A blanket licence already covers performances, so the per-performance
        # rate is quoted for reference and contributes nothing to the total.
        "per_performance_total_jpy": 0,
        "ec_streaming_surcharge_jpy": surcharge,
        "total_annual_jpy": annual_fee + surcharge,
        "note": NOTE_BLANKET,
    }


class LicenseFeeCalculateNode(FunctionNode):
    """Licence fee calculation node.

    Inner domain workflow node 3. Computes the applicable schedule from the
    resolved catalog records and the caller's declared usage profile.

    required_trust_level = ANONYMOUS (trust enforced at outer pre_process).

    Input state keys:
        sanitized_query:   str  — normalised query
        license_status:    str  — from JASRACNexToneSearchNode
        search_results:    list — records in scope
        validated_context: dict — accepted caller context (usage profile)

    Output state keys (partial dict):
        fee_schedule: Dict — the fee breakdown
        status:       AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:    (on error only) list of closed-set reason strings
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Compute the schedule. Returns a PARTIAL dict: only the keys written."""
        sanitized_query: str = state.get("sanitized_query", "") or ""
        license_status: str = state.get("license_status", "unknown") or "unknown"
        search_results: List[Dict[str, Any]] = state.get("search_results", []) or []

        if not sanitized_query.strip():
            emit_trace_event("license_fee_calculate_rejected", {"reason": REASON_EMPTY_QUERY}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"LicenseFeeCalculateNode: {REASON_EMPTY_QUERY}"],
            }

        context: Dict[str, Any] = state.get("validated_context") or {}
        profile: Dict[str, Any] = context.get("usage_profile") or default_profile()

        fee_schedule = compute_fee_schedule(license_status, search_results, profile)

        logger.info(
            "LicenseFeeCalculateNode: usage_type=%s stores=%d total=%d",
            fee_schedule["usage_type"],
            fee_schedule["store_count"],
            fee_schedule["total_annual_jpy"],
        )

        emit_trace_event(
            "license_fee_calculated",
            {
                "usage_type": fee_schedule["usage_type"],
                "store_count": fee_schedule["store_count"],
                "total_annual_jpy": fee_schedule["total_annual_jpy"],
                "blanket_available": fee_schedule["blanket_available"],
            },
            state,
        )

        return {
            "fee_schedule": fee_schedule,
            "status": AgentStatus.SUCCESS,
        }
