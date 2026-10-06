"""AgentCore Platform v1.0"""

# RET-C2-335 — AIBGMPolicyCheckNode
# Inner domain graph node 4: state the policy position that applies to the
# records in scope.
#
# Input:  state["search_results"]
# Output: state["ai_bgm_policy"]
#
# The decision reads the RECORDS, not the question. Whether a track was machine
# generated is a property of the track, and the caller declares it per record;
# scanning the query text for the word "ai" made "retail" a match in an earlier
# version and read a question about the AI policy as a track that was itself
# AI-generated.
#
# The three statements below are template-owned constants. Nothing derived from
# caller text is interpolated into them, which is what keeps this node out of
# the output-injection surface entirely.
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
# Policy statements. Sample text summarising the position a retailer has to work
# to; replace it with the wording your own licensing bodies publish.
# ---------------------------------------------------------------------------

POLICY_HUMAN_AUTHORED = (
    "Human-authored music: the standard blanket licence applies and no "
    "machine-generation restrictions are engaged. Commercial in-store use is "
    "permitted under the blanket agreement covering the premises."
)

POLICY_AI_GENERATED = (
    "Machine-generated music: "
    "1) a work with no identifiable human creative contribution is not covered "
    "by the blanket licence; "
    "2) per-performance licensing may apply where the generating platform has "
    "registered the work; "
    "3) commercial use on retail premises requires written confirmation from "
    "the platform that the work is licensed for it; "
    "4) digital and streaming channels require separate registration and "
    "per-stream royalties. "
    "Obtain explicit commercial-use confirmation from the platform before "
    "deploying machine-generated music in stores."
)

POLICY_UNKNOWN = (
    "The origin of the music in scope is not established. Confirm whether each "
    "track is human-authored or machine-generated before relying on any "
    "licence, and consult the relevant licensing body for confirmation."
)

REASON_NO_RECORDS = "no_records_in_scope"


def determine_policy(search_results: List[Dict[str, Any]]) -> str:
    """Return the policy statement that applies to the records in scope."""
    if not search_results:
        return POLICY_UNKNOWN
    if any(bool(record.get("ai_generated")) for record in search_results):
        return POLICY_AI_GENERATED
    return POLICY_HUMAN_AUTHORED


class AIBGMPolicyCheckNode(FunctionNode):
    """Machine-generated music policy node.

    Inner domain workflow node 4. Selects the policy statement that applies to
    the resolved catalog records.

    required_trust_level = ANONYMOUS (trust enforced at outer pre_process).

    Input state keys:
        search_results: list — records in scope from JASRACNexToneSearchNode

    Output state keys (partial dict):
        ai_bgm_policy: str — the applicable policy statement
        status:        AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:     (on error only) list of closed-set reason strings
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Select the policy. Returns a PARTIAL dict: only the keys written."""
        search_results: List[Dict[str, Any]] = state.get("search_results", []) or []

        if not search_results:
            emit_trace_event("ai_bgm_policy_check_rejected", {"reason": REASON_NO_RECORDS}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"AIBGMPolicyCheckNode: {REASON_NO_RECORDS}"],
            }

        policy = determine_policy(search_results)
        ai_applicable = any(bool(record.get("ai_generated")) for record in search_results)

        logger.info("AIBGMPolicyCheckNode: ai_applicable=%s", ai_applicable)

        emit_trace_event(
            "ai_bgm_policy_checked",
            {"ai_policy_applicable": ai_applicable, "record_count": len(search_results)},
            state,
        )

        return {
            "ai_bgm_policy": policy,
            "status": AgentStatus.SUCCESS,
        }
