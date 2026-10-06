"""AgentCore Platform v1.0"""

# RET-C2-335 — BGMLicensingWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the full BGM compliance and licensing search domain
# workflow:
#
#   START → query_sanitize → jasrac_nexttone_search → license_fee_calculate
#                                                             ↓
#                                                   ai_bgm_policy_check
#                                                             ↓
#                                                   license_report_format → END
#
# Called by BGMLicensingGraphNode.get_subgraph() in graph.py.
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   Inherits BaseGraph (fully custom topology — no forced backbone)
#   Implements all 7 BaseGraph ABC methods
#   register_nodes() does NOT call super() (abstract in BaseGraph)
#   Does NOT register initialize / finalize (outer backbone concerns)
#   get_output() designed together with BGMLicensingGraphNode.merge_output()
#   All inner nodes have required_trust_level = ANONYMOUS (review finding 5)
#   No L0 platform SDK imports

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph import context_bridge
from src.nodes.query_sanitize_node import QuerySanitizeNode
from src.nodes.jasrac_nexttone_search_node import JASRACNexToneSearchNode
from src.nodes.license_fee_calculate_node import LicenseFeeCalculateNode
from src.nodes.ai_bgm_policy_check_node import AIBGMPolicyCheckNode
from src.nodes.license_report_format_node import LicenseReportFormatNode
from src.schemas.state import State


class BGMLicensingWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for RET-C2-335.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by BGMLicensingGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          → query_sanitize            (QuerySanitizeNode)
          → jasrac_nexttone_search    (JASRACNexToneSearchNode)
          → license_fee_calculate     (LicenseFeeCalculateNode)
          → ai_bgm_policy_check       (AIBGMPolicyCheckNode)
          → license_report_format     (LicenseReportFormatNode)
          → END

    All nodes have required_trust_level = ANONYMOUS (CoE trust-trap rule #5).
    initialize / finalize are outer backbone concerns — not registered here.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "ret_c2_335_bgm_licensing_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        The inner graph consumes no config of its own: the caller-data bounds
        are read by src/services/input_guard.py from config/config.yaml, and the
        backbone values are validated by the outer graph. A deployment wiring a
        real licensing catalog would validate its connection settings here.
        """
        pass

    # ── Caller context across the subgraph boundary ───────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the caller's structured context into this graph's initial state.

        The framework calls a subgraph as ``invoke(user_input, session_id, ctx)``
        — there is no ``input_context`` parameter, so the value BaseGraph.invoke()
        puts into the initial state here is always the empty default. This hook
        runs after that state is built, so the value the outer node stashed wins.

        Without it every inner node would read an empty context on every request
        and the agent would silently answer from the shipped baseline catalog
        while the caller's own records sat unread in the outer state.
        """
        return {"input_context": context_bridge.current()}

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 5 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here is referenced in add_edges().
        All nodes instantiated with NO constructor args (SDK-v1 rule).
        """
        self._nodes["query_sanitize"] = QuerySanitizeNode()
        self._nodes["jasrac_nexttone_search"] = JASRACNexToneSearchNode()
        self._nodes["license_fee_calculate"] = LicenseFeeCalculateNode()
        self._nodes["ai_bgm_policy_check"] = AIBGMPolicyCheckNode()
        self._nodes["license_report_format"] = LicenseReportFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear BGM compliance domain topology.

        Linear pipeline: each node passes its partial-dict output into the
        shared State before the next node runs.  No conditional branching —
        all steps always execute (errors propagate via status field).
        route() is implemented as required by the ABC but never invoked.
        """
        self._sg.add_edge(START, "query_sanitize")
        self._sg.add_edge("query_sanitize", "jasrac_nexttone_search")
        self._sg.add_edge("jasrac_nexttone_search", "license_fee_calculate")
        self._sg.add_edge("license_fee_calculate", "ai_bgm_policy_check")
        self._sg.add_edge("ai_bgm_policy_check", "license_report_format")
        self._sg.add_edge("license_report_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by BaseGraph ABC.

        Linear-topology invariant: add_conditional_edges() is not used,
        so route() is never invoked at runtime.  Both branches return END
        to prevent an unexpected call from re-entering a mid-graph node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return END

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        Received by BGMLicensingGraphNode.merge_output() in graph.py as the
        sub_result argument.  Designed together with merge_output():

            Inner get_output()   emits: "result", "license_status",
                                        "fee_schedule", "status", ...
            Outer merge_output() reads: sub_result.get("result"),
                                        sub_result.get("license_status"),
                                        sub_result.get("fee_schedule")
        """
        return {
            "result": state.get("result"),
            "license_status": state.get("license_status"),
            "fee_schedule": state.get("fee_schedule", {}),
            "ai_bgm_policy": state.get("ai_bgm_policy"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
