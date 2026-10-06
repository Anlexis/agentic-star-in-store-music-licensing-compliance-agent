"""AgentCore Platform v1.0"""

# State is a flat TypedDict — never a Pydantic model. Checkpoints are serialised
# with msgpack, and a model instance corrupts silently on the way through.
# Extend the framework state with agent-specific fields only. No credentials,
# no secrets, no nested models.
#
# RET-C2-335 — retail background music licensing compliance.
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner domain
# workflow (BaseGraph). The fields below cover both layers, because the two
# graphs share one schema.
#
# Nothing here holds an API key, a token or personal data. Credentials reach a
# node through the invocation context, never through state.

from typing import Any, Dict, List, Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Flat TypedDict for RET-C2-335.

    All shared fields (user_input, input_context, status, session_id,
    node_history, error_log, formatted_output, hitl_*) are inherited from
    AgentState.
    """

    # ------------------------------------------------------------------
    # Outer layer — PreProcessNode / BGMLicensingGraphNode.merge_output
    # ------------------------------------------------------------------

    # The validated question, written by the input gate.
    validated_input: Optional[str]

    # The assembled compliance report. Written by merge_output() from the inner
    # graph's output; the output boundary reads it before anything is released.
    result: Optional[str]

    # ------------------------------------------------------------------
    # Inner layer — BGMLicensingWorkflowGraph
    # ------------------------------------------------------------------

    # QuerySanitizeNode output: the question normalised for catalog lookup.
    sanitized_query: Optional[str]

    # QuerySanitizeNode output: the accepted, bounded subset of input_context.
    # Keys: catalog_records (list of inert records), usage_profile (store_count,
    # monthly_performances, channel). Every downstream node reads this rather
    # than input_context, so there is exactly one point where the caller
    # contract is enforced.
    validated_context: Optional[Dict[str, Any]]

    # JASRACNexToneSearchNode output: the records in scope for this request.
    # Each record: {"track_id": str, "licensor": str, "license_type": str,
    #               "commercial_use": bool, "ai_generated": bool,
    #               "registration_id": str (shipped sample records only)}
    search_results: Optional[List[Dict[str, Any]]]

    # JASRACNexToneSearchNode output: where those records came from —
    # "caller" or "baseline_sample". The report states which, because an answer
    # about the shipped sample is not an answer about the caller's repertoire.
    catalog_source: Optional[str]

    # JASRACNexToneSearchNode output: overall status derived from the records.
    # One of "licensed" | "partial" | "unlicensed" | "unknown".
    license_status: Optional[str]

    # LicenseFeeCalculateNode output: the computed charges.
    # Keys: usage_type, store_count, annual_performances, annual_fee_jpy,
    #       blanket_available, per_performance_fee_jpy,
    #       per_performance_total_jpy, ec_streaming_surcharge_jpy,
    #       total_annual_jpy, note
    fee_schedule: Optional[Dict[str, Any]]

    # AIBGMPolicyCheckNode output: the applicable policy statement.
    ai_bgm_policy: Optional[str]

    # ------------------------------------------------------------------
    # Tracing — framework-managed; node code does not write these.
    # ------------------------------------------------------------------

    trace_id: Optional[str]
    correlation_id: Optional[str]
    # node_history is inherited from AgentState.
