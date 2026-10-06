"""AgentCore Platform v1.0"""

# RET-C2-335 — carrying the caller's context across the subgraph boundary.
#
# The framework's GraphNode calls the inner graph as
# ``subgraph.invoke(user_input, session_id=..., ctx=...)``. There is no
# ``input_context`` parameter in that call, so an agent whose domain work lives
# in an inner graph loses the caller's structured data at the boundary: the
# outer state has it, the inner state is handed ``{}``, and every inner node
# reads an empty mapping while the request looked well-formed all the way in.
#
# The bridge is a ContextVar. The outer node stashes the context as it extracts
# the query; the inner graph seeds its own initial state from the same variable
# through ``_extra_initial_state()``, which BaseGraph.invoke() applies after it
# has built the initial state — so the seeded value wins over the empty default.
#
# A ContextVar rather than a module global: it is per-context, so two requests
# served concurrently by the same process cannot read each other's data, and
# asyncio tasks each get their own copy.

from contextvars import ContextVar, Token
from typing import Any, Dict

_CALLER_CONTEXT: ContextVar[Dict[str, Any]] = ContextVar("ret_c2_335_caller_context")


def stash(context: Dict[str, Any]) -> "Token[Dict[str, Any]]":
    """Record the validated caller context for the inner graph to pick up.

    Returns the token so a caller that needs to can restore the previous value;
    the outer node does not, because each invocation sets it before the inner
    graph runs and nothing reads it afterwards.
    """
    return _CALLER_CONTEXT.set(dict(context) if isinstance(context, dict) else {})


def current() -> Dict[str, Any]:
    """The caller context for this execution context, or an empty mapping.

    Empty is a legitimate answer, not a failure: a request that carries no
    context degrades to the shipped baseline catalog, which is the documented
    behaviour.
    """
    try:
        return dict(_CALLER_CONTEXT.get())
    except LookupError:
        return {}


def reset(token: "Token[Dict[str, Any]]") -> None:
    """Restore the value the ContextVar held before ``stash``."""
    _CALLER_CONTEXT.reset(token)
