"""AgentCore Platform v1.0"""

# RET-C2-335 — PreProcessNode
# Outer backbone pre_process slot — the input gate.
#
# Validates the licensing question before the domain workflow runs:
#   1. type guard — reject a non-string user_input FIRST, before any str op;
#   2. empty / whitespace-only / length bounds;
#   3. injection screen — directive phrases AND chat-template control tokens,
#      raw and normalised (src/services/input_guard.py, shared with the node
#      that owns the caller contract so there is one definition of the class);
#   4. personal-data screen — an email address, phone number or card number has
#      no place in a question about background music.
#
# required_trust_level = VERIFIED_EXTERNAL — this is the external-facing gate.
# Inner nodes are ANONYMOUS because the trust decision is made here, once.
#
# A rejection names the CLASS, never the value. An error entry quoting the
# rejected text puts the very thing the screen just refused into the audit
# trail, and error entries are one field away from the caller's envelope.
#
# Every rejection path and the success path emit an audit event.

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.services.input_guard import screen_injection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Length bounds.
# ---------------------------------------------------------------------------
MAX_QUERY_LENGTH = 500
MIN_QUERY_LENGTH = 3

# ---------------------------------------------------------------------------
# Closed-set rejection reasons.
# ---------------------------------------------------------------------------
REASON_NON_STRING = "non_string_input"
REASON_EMPTY = "empty_input"
REASON_BLANK = "blank_input"
REASON_TOO_SHORT = "too_short"
REASON_TOO_LONG = "too_long"
REASON_INJECTION = "injection_screen"
REASON_PERSONAL_DATA = "personal_data_in_query"

# ---------------------------------------------------------------------------
# Personal-data markers. Each requires enough structure that the digit-free
# natural-language questions this agent exists to answer cannot match.
# ---------------------------------------------------------------------------
_PII_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # Card number: 13-19 digits, optionally grouped. Checked before the phone
    # pattern so the longer run is attributed to the more specific class.
    ("card_number", re.compile(r"\b\d(?:[ -]?\d){12,18}\b")),
    # Phone: a 10-11 digit run, optionally grouped.
    ("phone", re.compile(r"(?<![\w-])\+?\d(?:[ -]?\d){9,10}(?![\w-])")),
]


def scan_personal_data(text: str) -> Optional[str]:
    """Return the first matched personal-data class, or None."""
    for name, pattern in _PII_PATTERNS:
        if pattern.search(text):
            return name
    return None


class PreProcessNode(FunctionNode):
    """The input gate for RET-C2-335.

    Outer backbone pre_process slot. Validates the plain-language licensing
    question before the domain workflow runs.

    required_trust_level = VERIFIED_EXTERNAL: only a caller an upstream has
    vouched for may reach the licensing search. Inner nodes are ANONYMOUS
    because the trust gate is enforced here.

    Input state keys:
        user_input: str — the licensing question

    Output state keys (partial dict):
        validated_input: str — trimmed, validated question
        status:          AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:       (on error only) list of closed-set reason strings
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _reject(self, state: Dict[str, Any], reason: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        emit_trace_event("pre_process_rejected", {"reason": reason, **(payload or {})}, state)
        return {
            "status": AgentStatus.ERROR,
            "error_log": [f"PreProcessNode: {reason}"],
        }

    def execute(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Validate and trim the question. Returns a PARTIAL dict."""
        user_input = state.get("user_input", "")

        # --- Type guard FIRST: reject a non-string before any string operation.
        if not isinstance(user_input, str):
            return self._reject(state, REASON_NON_STRING, {"input_type": type(user_input).__name__})

        if not user_input:
            return self._reject(state, REASON_EMPTY)

        stripped = user_input.strip()

        if not stripped:
            return self._reject(state, REASON_BLANK)

        if len(stripped) < MIN_QUERY_LENGTH:
            return self._reject(state, REASON_TOO_SHORT, {"query_length": len(stripped)})

        if len(stripped) > MAX_QUERY_LENGTH:
            return self._reject(state, REASON_TOO_LONG, {"query_length": len(stripped)})

        injection = screen_injection(stripped)
        if injection:
            logger.warning("PreProcessNode: question refused (%s)", injection)
            return self._reject(state, REASON_INJECTION, {"pattern": injection})

        personal = scan_personal_data(stripped)
        if personal:
            logger.warning("PreProcessNode: question refused (%s)", personal)
            return self._reject(state, REASON_PERSONAL_DATA, {"pattern": personal})

        logger.info("PreProcessNode: question accepted length=%d", len(stripped))

        emit_trace_event("pre_process_validated", {"query_length": len(stripped)}, state)

        return {
            "validated_input": stripped,
            "status": AgentStatus.SUCCESS,
        }
