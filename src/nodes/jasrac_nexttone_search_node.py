"""AgentCore Platform v1.0"""

# RET-C2-335 — JASRACNexToneSearchNode
# Inner domain graph node 2: resolve the queried background music against a
# licensing catalog and derive the overall licensing status.
#
# Input:  state["sanitized_query"]   — normalised query
#         state["validated_context"] — the caller's own catalog records, already
#                                      bounded and inert (QuerySanitizeNode)
# Output: state["search_results"], state["license_status"]
#
# Two sources, one code path. When the request carries catalog records they ARE
# the catalog: the agent answers about the caller's own shelf of tracks and
# every licensing status is reachable from real data. When it carries none, the
# node falls back to a small shipped sample so the template runs end to end out
# of the box — that sample is a demonstration, not an answer about anyone's
# actual repertoire, and the report says so.
#
# required_trust_level = ANONYMOUS (trust enforced at outer pre_process)

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shipped sample catalog. Deterministic, and used only when the request carries
# no records of its own. Identifiers here are template-owned constants, which is
# why they may use characters the caller's own identifiers may not.
#
# The sample deliberately includes an entry that is NOT cleared for commercial
# use: without one, "unlicensed" and "partial" would be structurally
# unreachable on the sample path and the status field would be decorative.
# ---------------------------------------------------------------------------

BASELINE_CATALOG: List[Dict[str, Any]] = [
    {
        "track_id": "bolero_ravel",
        "registration_id": "SAMPLE-001-12345",
        "licensor": "jasrac",
        "license_type": "blanket",
        "commercial_use": True,
        "ai_generated": False,
    },
    {
        "track_id": "ai_bgm_pack_vol_1",
        "registration_id": "SAMPLE-AI-00892",
        "licensor": "nextone",
        "license_type": "per_performance",
        "commercial_use": True,
        "ai_generated": True,
    },
    {
        "track_id": "stock_ambient_loop",
        "registration_id": "SAMPLE-STK-77891",
        "licensor": "jasrac",
        "license_type": "blanket",
        "commercial_use": True,
        "ai_generated": False,
    },
    {
        "track_id": "unregistered_demo_track",
        "registration_id": "SAMPLE-UNREG-00001",
        "licensor": "other",
        "license_type": "none",
        "commercial_use": False,
        "ai_generated": False,
    },
]

GENERIC_BASELINE_RECORD: Dict[str, Any] = {
    "track_id": "generic_in_store_bgm",
    "registration_id": "SAMPLE-BLANKET-RETAIL",
    "licensor": "jasrac",
    "license_type": "blanket",
    "commercial_use": True,
    "ai_generated": False,
}

# Every identifier the template itself can render. The output boundary uses this
# to tell a template constant from caller data without re-deriving the rule.
BASELINE_REGISTRATION_IDS = frozenset(
    [record["registration_id"] for record in BASELINE_CATALOG] + [GENERIC_BASELINE_RECORD["registration_id"]]
)

STATUS_LICENSED = "licensed"
STATUS_PARTIAL = "partial"
STATUS_UNLICENSED = "unlicensed"
STATUS_UNKNOWN = "unknown"

REASON_EMPTY_QUERY = "empty_query"


def _search_baseline(query: str) -> List[Dict[str, Any]]:
    """Match the shipped sample catalog against the query.

    Matching is on the identifier and the licensor name, both of which are
    lower-case tokens — so this is a substring test over normalised text, not a
    ranking model. A request that matches nothing gets the generic blanket entry
    that describes ordinary in-store background music.
    """
    lowered = query.lower()
    results = [
        record
        for record in BASELINE_CATALOG
        if record["track_id"].replace("_", " ") in lowered
        or record["track_id"] in lowered
        or record["registration_id"].lower() in lowered
    ]
    if not results:
        results = [dict(GENERIC_BASELINE_RECORD)]
    return [dict(record) for record in results]


def derive_license_status(results: List[Dict[str, Any]]) -> str:
    """Derive the overall status from the records in scope.

    A record counts as cleared when the caller says it may be used commercially
    AND a licence type is actually named. "none" with commercial_use True is a
    claim with nothing behind it, and reading it as cleared is how an agent ends
    up telling a shop it is covered when it is not.
    """
    if not results:
        return STATUS_UNKNOWN
    cleared = [bool(record.get("commercial_use")) and record.get("license_type") != "none" for record in results]
    if all(cleared):
        return STATUS_LICENSED
    if any(cleared):
        return STATUS_PARTIAL
    return STATUS_UNLICENSED


class JASRACNexToneSearchNode(FunctionNode):
    """Licensing catalog resolution node.

    Inner domain workflow node 2. Resolves the query against the caller's own
    catalog records when the request carries them, and against the shipped
    sample catalog otherwise, then derives the overall licensing status.

    required_trust_level = ANONYMOUS (trust enforced at outer pre_process).

    Input state keys:
        sanitized_query:   str  — normalised query from QuerySanitizeNode
        validated_context: dict — accepted caller context from QuerySanitizeNode

    Output state keys (partial dict):
        search_results:  List[Dict] — the records in scope for this request
        license_status:  str — licensed | partial | unlicensed | unknown
        status:          AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:       (on error only) list of closed-set reason strings
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Resolve the catalog. Returns a PARTIAL dict: only the keys written."""
        sanitized_query: str = state.get("sanitized_query", "") or ""

        if not sanitized_query.strip():
            emit_trace_event("jasrac_nexttone_search_rejected", {"reason": REASON_EMPTY_QUERY}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"JASRACNexToneSearchNode: {REASON_EMPTY_QUERY}"],
            }

        context: Dict[str, Any] = state.get("validated_context") or {}
        caller_records: List[Dict[str, Any]] = list(context.get("catalog_records") or [])

        if caller_records:
            results = [dict(record) for record in caller_records]
            source = "caller"
        else:
            results = _search_baseline(sanitized_query)
            source = "baseline_sample"

        license_status = derive_license_status(results)

        logger.info(
            "JASRACNexToneSearchNode: %d record(s) from %s, status=%s",
            len(results),
            source,
            license_status,
        )

        emit_trace_event(
            "jasrac_nexttone_search_complete",
            {
                "result_count": len(results),
                "license_status": license_status,
                "catalog_source": source,
            },
            state,
        )

        return {
            "search_results": results,
            "license_status": license_status,
            "catalog_source": source,
            "status": AgentStatus.SUCCESS,
        }
