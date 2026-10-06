"""AgentCore Platform v1.0"""

# RET-C2-335 — Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed — identical to Cat 1, do NOT override add_edges()):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max_retry)
#                                          pre_process
#
#   `main` slot is a GraphNode subclass (BGMLicensingGraphNode) that delegates
#   the full domain workflow to BGMLicensingWorkflowGraph (inner BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 ← outer graph (this file)
#   src/graph/domain_workflow_graph.py ← inner graph (BGMLicensingWorkflowGraph)
#   src/graph/context_bridge.py        ← caller context across the subgraph boundary
#
# Class name: RETMusicBGMLicensingSearchAgent
#   Must match config/agent.yaml `class:` and src/api/server.py import exactly.
#
# Rules enforced:
#   RETMusicBGMLicensingSearchAgent inherits AgentBaseGraph (L1 Base)
#   super().register_nodes() called first (fills initialize + finalize)
#   BGMLicensingGraphNode assigned to self._nodes["main"]
#   merge_output() returns only changed keys
#   Inner node trust is ANONYMOUS — the trust gate is enforced once, at pre_process
#   add_edges() NOT overridden on the outer graph
#   No platform SDK imports

from typing import Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.graph import context_bridge
from src.nodes.post_process_node import WITHHELD_NOTICE, PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class BGMLicensingGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of RETMusicBGMLicensingSearchAgent.

    Wraps BGMLicensingWorkflowGraph (inner Cat 2 BaseGraph).
    Called by AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()    — instantiate and return BGMLicensingWorkflowGraph
      extract_input()   — pull validated_input from outer state (string query),
                          and stash the caller context for the inner graph
      merge_output()    — map sub_result fields into outer state delta (changed keys only)
      error_strategy    — "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner BGM licensing workflow graph.

        Lazily imported to avoid circular-import risk at module load time.
        """
        from src.graph.domain_workflow_graph import BGMLicensingWorkflowGraph

        return BGMLicensingWorkflowGraph()

    def extract_input(self, state: AgentState) -> str:
        """Return the string user_input to pass into inner_graph.invoke().

        PreProcessNode validates the raw user_input and writes validated_input.
        Prefer validated_input; fall back to user_input for resilience.

        The caller's structured context is stashed here rather than passed: the
        framework's subgraph call has no parameter for it, so without this the
        inner graph would run against an empty context on every request while
        the outer state still held the caller's data.
        """
        context_bridge.stash(dict(state.get("input_context") or {}))
        chosen: str = state.get("validated_input") or state.get("user_input", "")
        return chosen

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by BGMLicensingWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.

        Key coupling (designed together with BGMLicensingWorkflowGraph.get_output()):
          Inner get_output() emits  → "result", "license_status", "fee_schedule"
          This merge_output() reads → sub_result.get(...)
        """
        return {
            "result": sub_result.get("result"),
            "license_status": sub_result.get("license_status"),
            "fee_schedule": sub_result.get("fee_schedule", {}),
        }


class RETMusicBGMLicensingSearchAgent(AgentBaseGraph):
    """Outer graph for RET-C2-335 — retail ambient-music licensing compliance.

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is fully
    encapsulated in BGMLicensingGraphNode (main slot), which delegates to
    BGMLicensingWorkflowGraph (inner BaseGraph).

    Backbone (fixed — identical to Cat 1):
        START → initialize → pre_process → main → post_process → finalize → END

    register_nodes() is the ONLY node override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process: PreProcessNode (input validation; VERIFIED_EXTERNAL)
      - main:        BGMLicensingGraphNode (delegates to BGMLicensingWorkflowGraph)
      - post_process: PostProcessNode (output boundary; ANONYMOUS)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with the platform registry."""
        return "RETMusicBGMLicensingSearchAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default InitializeNode and FinalizeNode.
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = BGMLicensingGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Resolve the caller-facing envelope, releasing nothing on a failure.

        The framework resolves the answer as ``formatted_output or result`` with
        no status check. That is correct for the success path and unsafe for
        every other one, because the backbone routes a non-success status
        straight to ``finalize`` — the output boundary is SKIPPED. Anything
        already sitting in ``result`` would then ship inside the error envelope
        without ever having been checked.

        This override is the only containment for those routes: an input
        rejection at pre_process and a subgraph failure at main both reach
        ``finalize`` without passing post_process. The output boundary's own
        clearing is a different layer covering a different path — a violation
        found DURING the boundary check — and is asserted at the node level so
        that neither layer's tests can be satisfied by the other.

        The notice is truthy on purpose: a falsy replacement re-opens the
        framework's fallback onto whatever survived in state.
        """
        output: Dict[str, Any] = dict(super().get_output(state))
        if state.get("status") != AgentStatus.SUCCESS.value:
            output["output"] = WITHHELD_NOTICE
        return output

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


# Module-level alias so callers can import `from src.graph.graph import Graph`.
Graph = RETMusicBGMLicensingSearchAgent
