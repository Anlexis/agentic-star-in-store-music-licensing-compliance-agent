"""AgentCore Platform v1.0"""

# RET-C2-335 — QuerySanitizeNode
# Inner domain graph node 1: the node that owns the caller contract.
#
# Input:  state["user_input"]     — validated query from the outer pre_process
#         state["input_context"]  — the caller's own catalog records and usage
#                                   profile, seeded across the subgraph boundary
# Output: state["sanitized_query"], state["validated_context"]
#
# Two things happen here, and both are REFUSALS rather than repairs:
#
#   - the query is screened for directive injection and chat-template control
#     tokens, raw and normalised. An earlier version of this node STRIPPED the
#     matched phrases and forwarded the remainder. That is worse than doing
#     nothing: it converts a detectable attack into undetectable plain text, and
#     it mangles legitimate licensing prose on the way through — "does this act
#     as a public performance" is an ordinary question, and deleting "act as"
#     answers one nobody asked.
#   - the caller context is validated field by field against explicit bounds.
#     The entry point performs the same check so a bad request is refused with a
#     4xx before the graph runs, but the guarantee belongs HERE: when the agent
#     runs behind the platform gateway the entry point is not in the path at all.
#
# required_trust_level = ANONYMOUS — the trust gate is enforced once, at the
# outer pre_process slot, so the inner nodes do not re-gate it.

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.services.input_guard import screen_injection, validate_context

logger = logging.getLogger(__name__)

_MAX_KB_QUERY_LENGTH = 300  # catalog lookup maximum

# Closed-set rejection reasons. Nothing derived from caller content is ever
# placed in an error entry: the reason names the class, and the field name (when
# there is one) has already been checked inert by the guard.
REASON_EMPTY_INPUT = "empty_user_input"
REASON_EMPTY_AFTER_NORMALISATION = "empty_after_normalisation"
REASON_INJECTION = "injection_screen"
REASON_CONTEXT_REJECTED = "context_contract"


def _normalise_query(raw: str) -> str:
    """Collapse whitespace and truncate to the catalog lookup limit.

    Normalisation only. Nothing is deleted for security reasons here — that is
    the screen's job, and its answer is yes or no, never a rewrite.
    """
    text = re.sub(r"[ \t]+", " ", raw).strip()
    if len(text) > _MAX_KB_QUERY_LENGTH:
        text = text[:_MAX_KB_QUERY_LENGTH].rstrip()
    return text


class QuerySanitizeNode(FunctionNode):
    """Screen the query, validate the caller context, normalise for lookup.

    Inner domain workflow node 1. Receives the validated query from the outer
    pre_process layer plus the caller's structured context, and produces the
    normalised query and the accepted context the rest of the pipeline reads.

    required_trust_level = ANONYMOUS (trust enforced at outer pre_process).

    Input state keys:
        user_input:    str  — validated query (set by outer PreProcessNode)
        input_context: dict — caller catalog records and usage profile

    Output state keys (partial dict):
        sanitized_query:   str  — normalised query
        validated_context: dict — the accepted, bounded subset of input_context
        status:            AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:         (on error only) list of closed-set reason strings
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Screen and normalise. Returns a PARTIAL dict: only the keys written."""
        raw_query: str = state.get("user_input", "") or ""

        if not raw_query.strip():
            emit_trace_event("bgm_query_sanitize_rejected", {"reason": REASON_EMPTY_INPUT}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"QuerySanitizeNode: {REASON_EMPTY_INPUT}"],
            }

        # Screen BEFORE normalising and again after: a control token is visible
        # in the raw text, a spliced directive only once markup is folded away.
        injection = screen_injection(raw_query)
        if injection:
            logger.warning("QuerySanitizeNode: query refused (%s)", injection)
            emit_trace_event(
                "bgm_query_sanitize_rejected",
                {"reason": REASON_INJECTION, "pattern": injection},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"QuerySanitizeNode: {REASON_INJECTION}"],
            }

        normalised = _normalise_query(raw_query)

        if not normalised:
            emit_trace_event(
                "bgm_query_sanitize_rejected",
                {"reason": REASON_EMPTY_AFTER_NORMALISATION},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"QuerySanitizeNode: {REASON_EMPTY_AFTER_NORMALISATION}"],
            }

        post_injection = screen_injection(normalised)
        if post_injection:
            logger.warning("QuerySanitizeNode: query refused after normalisation (%s)", post_injection)
            emit_trace_event(
                "bgm_query_sanitize_rejected",
                {"reason": REASON_INJECTION, "pattern": post_injection},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"QuerySanitizeNode: {REASON_INJECTION}"],
            }

        accepted, refusals = validate_context(state.get("input_context"))
        if refusals:
            reason, field = refusals[0]
            logger.warning("QuerySanitizeNode: context refused (%s on %s)", reason, field)
            emit_trace_event(
                "bgm_context_rejected",
                {"reason": reason, "field": field, "refusal_count": len(refusals)},
                state,
            )
            errors: List[str] = [f"QuerySanitizeNode: {REASON_CONTEXT_REJECTED} — {reason} on input_context.{field}"]
            return {"status": AgentStatus.ERROR, "error_log": errors}

        logger.info(
            "QuerySanitizeNode: query accepted length=%d records=%d",
            len(normalised),
            len(accepted.get("catalog_records", [])),
        )

        emit_trace_event(
            "bgm_query_sanitized",
            {
                "original_length": len(raw_query),
                "sanitized_length": len(normalised),
                "caller_record_count": len(accepted.get("catalog_records", [])),
            },
            state,
        )

        return {
            "sanitized_query": normalised,
            "validated_context": accepted,
            "status": AgentStatus.SUCCESS,
        }
