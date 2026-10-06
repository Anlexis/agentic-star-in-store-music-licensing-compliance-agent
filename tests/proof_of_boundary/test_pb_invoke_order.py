# PB-6: Backbone Invoke Order — Graph().invoke() end-to-end
#
# Verifies the 5-node backbone is fully traversed in order on a
# SUCCESS-yielding payload with a real VERIFIED_EXTERNAL caller.
#
# CRITICAL: invoke MUST use InvocationContext(caller_trust_level=VERIFIED_EXTERNAL),
# never InvocationContext.for_internal().  An INTERNAL context masks the inner-node
# trust gate and lets a real VERIFIED_EXTERNAL STG invoke fail silently.
#
# Two template-specific parts (fill per build):
#   _MAIN_SLOT_NODE   — the class name placed in the "main" slot of register_nodes()
#   _VALID_PAYLOAD    — a domain input that yields AgentStatus.SUCCESS end-to-end
#
# The rest is boilerplate (canonical: a peer template test_pb_invoke_order.py).


from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph

# ── Template-specific (fill two values only) ──────────────────────────────────

_MAIN_SLOT_NODE = "BGMLicensingGraphNode"

# The same question deploy/invoke_payload.json posts and the integration suite
# asserts against, so the backbone test, the deployment probe and the end-to-end
# suite cannot drift into three different contracts. Held to the payload file by
# tests/integration/test_invoke_e2e.py.
_VALID_PAYLOAD = (
    "Is the background music we play in store covered by a blanket licence, " "and what does it cost for the year?"
)

# ── Boilerplate (do NOT modify) ────────────────────────────────────────────────

_EXPECTED_BACKBONE = [
    "InitializeNode",
    "PreProcessNode",
    _MAIN_SLOT_NODE,
    "PostProcessNode",
    "FinalizeNode",
]


def _node_names(node_history: list) -> list[str]:
    """Normalise node_history entries to class-name strings."""
    result = []
    for entry in node_history:
        if isinstance(entry, str):
            result.append(entry)
        elif isinstance(entry, dict):
            result.append(entry.get("node") or entry.get("name") or str(entry))
        else:
            result.append(type(entry).__name__)
    return result


class TestBackboneInvokeOrder:
    """PB-6: full Graph().invoke() backbone-order verification."""

    def test_backbone_traversal_order_on_success(self):
        """All 5 backbone nodes must appear in the correct order for a SUCCESS payload."""
        graph = Graph()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = graph.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        status = result.get("status")
        assert status == AgentStatus.SUCCESS, (
            f"Expected SUCCESS but got {status!r}. " f"error_log: {result.get('error_log')}"
        )

        history = _node_names(result.get("node_history", []))

        for backbone_node in _EXPECTED_BACKBONE:
            assert backbone_node in history, (
                f"Backbone node {backbone_node!r} not in node_history. " f"Visited: {history}"
            )

        # Assert strict order: each backbone node must appear after the previous one.
        positions: dict[str, int] = {}
        for i, name in enumerate(history):
            if name in _EXPECTED_BACKBONE and name not in positions:
                positions[name] = i

        ordered_by_pos = sorted(
            [n for n in _EXPECTED_BACKBONE if n in positions],
            key=lambda n: positions[n],
        )
        assert ordered_by_pos == _EXPECTED_BACKBONE, (
            f"Backbone order violation. Expected {_EXPECTED_BACKBONE}, "
            f"got {ordered_by_pos}. Full history: {history}"
        )

    def test_success_payload_produces_output(self):
        """A valid BGM compliance query must produce non-empty output on SUCCESS."""
        graph = Graph()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = graph.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        assert result.get("status") == AgentStatus.SUCCESS
        output = result.get("output", "")
        assert output, "output should be non-empty for a SUCCESS result. " f"Got: {output!r}"

    def test_verified_external_context_is_admitted(self):
        """VERIFIED_EXTERNAL caller must not be denied by inner ANONYMOUS trust gates."""
        graph = Graph()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = graph.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        status = result.get("status")
        error_log = result.get("error_log", [])
        assert status != AgentStatus.ERROR or not any(
            "trust" in str(e).lower() or "unauthorized" in str(e).lower() for e in error_log
        ), "VERIFIED_EXTERNAL caller was denied — inner node trust gate is too restrictive. " f"error_log: {error_log}"
