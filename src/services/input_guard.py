"""AgentCore Platform v1.0"""

# RET-C2-335 — the caller-data contract, and the screens that hold it.
#
# Two caller channels reach this agent and each one is hostile until proven
# bounded:
#
#   user_input     — the plain-language licensing question. Screened for
#                    directive injection and for chat-template control tokens,
#                    both in the raw text and in the whitespace-normalised form,
#                    and REFUSED rather than stripped.
#   input_context  — the caller's own licensing catalog and usage profile.
#                    Every field is checked against an explicit bound: an inert
#                    identifier alphabet, a closed enum, or a finite numeric
#                    range. Unknown keys are refused outright.
#
# Why refuse rather than strip. A screen that deletes a directive and forwards
# the remainder turns a detectable attack into undetectable plain text, and it
# corrupts legitimate text on the way through: "does this act as a public
# performance" is an ordinary licensing question, and a stripper that removes
# "act as" answers a question nobody asked. Refusal is both safer and honest.
#
# Why the context channel is refused, not filtered. An unsupported key is not
# merely ignored downstream. It stays on the context channel, the framework's
# first node returns that channel verbatim inside its own result, and the
# framework's output scan then fails the whole run with an error the caller
# cannot act on. The request cannot succeed either way, so it is refused here
# with the field named.
#
# A refusal names the FIELD, never the value. Field names are caller data too:
# a name is echoed only when it is itself inert, otherwise the refusal is
# positional.

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from framework.security.credential_detector import detect_credentials_in_value

from src.services.runtime_config import catalog_limits

# ---------------------------------------------------------------------------
# Closed-set refusal reasons. These strings, and nothing derived from caller
# content, are all that leaves this module.
# ---------------------------------------------------------------------------
REASON_NOT_A_MAPPING = "not_a_mapping"
REASON_UNKNOWN_FIELD = "unknown_field"
REASON_WRONG_TYPE = "wrong_type"
REASON_NOT_INERT = "not_an_inert_identifier"
REASON_NOT_IN_ENUM = "not_an_allowed_value"
REASON_NOT_FINITE = "not_a_finite_number_in_range"
REASON_TOO_MANY_RECORDS = "too_many_records"
REASON_CREDENTIAL_SHAPED = "credential_shaped_value"
REASON_TOO_LARGE = "context_too_large"

# Injection screen labels.
INJECTION_DIRECTIVE = "directive_phrase"
INJECTION_CONTROL_TOKEN = "chat_template_control_token"
INJECTION_MARKUP = "markup_or_script"
INJECTION_SQL = "sql_statement"

# ---------------------------------------------------------------------------
# The inert alphabet for every caller string that can be rendered into the
# report. Lower-case letters, digits, underscore and hyphen only — no
# whitespace, no markup, no pipe (the report renders a markdown table, and a
# pipe would let a caller forge a column).
# ---------------------------------------------------------------------------
_INERT_RE = re.compile(r"\A[a-z0-9_-]{1,32}\Z")

# Serialized size ceiling on the whole context object, before any field is read.
_MAX_CONTEXT_BYTES = 64 * 1024

# ---------------------------------------------------------------------------
# Closed enums for the catalog contract.
# ---------------------------------------------------------------------------
LICENSORS = ("jasrac", "nextone", "other")
LICENSE_TYPES = ("blanket", "per_performance", "none")
CHANNELS = ("physical_retail", "ec_streaming", "both")

_RECORD_FIELDS = ("track_id", "licensor", "license_type", "commercial_use", "ai_generated")
_PROFILE_FIELDS = ("store_count", "monthly_performances", "channel")
_CONTEXT_FIELDS = ("catalog_records", "usage_profile")

# Keys the platform itself puts into input_context, not the caller. The Marketplace
# runner invokes every agent as
#     agent.invoke(message, ctx=ctx, input_context={"conversation_history": history})
# (agenticstar-agentcore, shared/bootstrap/marketplace_app.py), whatever the user typed.
# Refusing it as an unknown field refused every chat request before the question was
# read. Discarded, not validated: nothing in this pipeline reads prior turns, and
# screening a transcript would let one earlier message refuse every later one. Discarding
# adds no exposure — the backbone's first node has already copied the raw input_context
# into state before this contract runs.
PLATFORM_RESERVED_KEYS = frozenset({"conversation_history"})

# ---------------------------------------------------------------------------
# Injection patterns.
#
# Anchored to statement shapes rather than bare verbs, so ordinary licensing
# prose survives: "insert into" alone is a SQL verb pair and also an English
# one, so it is required to be followed by an identifier and a parenthesis or
# VALUES clause. The corpus these are probed against is the repository's own
# fixtures, not invented sentences.
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # Chat-template control tokens. These are a CLASS, not a list of phrases:
    # the framework blocks <|im_start|> and [INST]/[SYS] but scores <<SYS>>
    # nothing at all, which is exactly what makes the angle-bracket form the
    # one that gets through a phrase-based screen.
    (INJECTION_CONTROL_TOKEN, re.compile(r"<\|[^|>]{0,64}\|>")),
    (INJECTION_CONTROL_TOKEN, re.compile(r"\[/?\s*(?:INST|SYS|SYSTEM)\s*\]", re.IGNORECASE)),
    (INJECTION_CONTROL_TOKEN, re.compile(r"<<\s*/?\s*(?:SYS|SYSTEM)\s*>>", re.IGNORECASE)),
    (INJECTION_CONTROL_TOKEN, re.compile(r"<\|?(?:endoftext|start_header_id|eot_id)\|?>", re.IGNORECASE)),
    # Directive injection.
    # "…ignore the previous instructions". The object noun is REQUIRED here, so
    # "should we ignore previous agreements?" — a real licensing question — does
    # not match.
    (
        INJECTION_DIRECTIVE,
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+|the\s+|any\s+)?"
            r"(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?|context|messages?)\b",
            re.IGNORECASE,
        ),
    ),
    # "…forget everything above". No noun follows, so the object has to carry
    # the weight: only the totalising forms match, never "ignore that track".
    (
        INJECTION_DIRECTIVE,
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:everything|all)\s+"
            r"(?:that\s+came\s+)?(?:above|before|prior|previous|earlier)\b",
            re.IGNORECASE,
        ),
    ),
    (INJECTION_DIRECTIVE, re.compile(r"\byou\s+are\s+now\s+(?:a|an|the)\b", re.IGNORECASE)),
    (INJECTION_DIRECTIVE, re.compile(r"\b(?:system|developer)\s+prompt\b", re.IGNORECASE)),
    (INJECTION_DIRECTIVE, re.compile(r"\bjailbreak\b", re.IGNORECASE)),
    (
        INJECTION_DIRECTIVE,
        re.compile(r"\breveal\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)\b", re.IGNORECASE),
    ),
    (
        INJECTION_DIRECTIVE,
        re.compile(
            r"\b(?:act|behave|respond)\s+as\s+(?:if\s+you\s+(?:are|were)|an?\s+\w+\s+(?:bot|agent|assistant|model))\b",
            re.IGNORECASE,
        ),
    ),
    # Markup / script.
    (INJECTION_MARKUP, re.compile(r"<\s*script\b", re.IGNORECASE)),
    (INJECTION_MARKUP, re.compile(r"javascript\s*:", re.IGNORECASE)),
    (INJECTION_MARKUP, re.compile(r"\bon(?:error|load|click|mouseover)\s*=", re.IGNORECASE)),
    # SQL statements. Each requires enough structure that an English sentence
    # carrying the same words cannot match.
    (INJECTION_SQL, re.compile(r"\bunion\s+(?:all\s+)?select\b", re.IGNORECASE)),
    (INJECTION_SQL, re.compile(r"\b(?:drop|truncate)\s+table\s+\w+", re.IGNORECASE)),
    (INJECTION_SQL, re.compile(r"\binsert\s+into\s+\w+\s*[(]", re.IGNORECASE)),
    (INJECTION_SQL, re.compile(r"\bdelete\s+from\s+\w+\s+where\b", re.IGNORECASE)),
    (INJECTION_SQL, re.compile(r"'\s*or\s*'?\d+'?\s*=\s*'?\d+", re.IGNORECASE)),
    (INJECTION_SQL, re.compile(r"\bor\s+1\s*=\s*1\b", re.IGNORECASE)),
]

# Markup that a naive strip would remove, re-assembling a directive that was
# spliced apart to evade a raw scan ("ig<b>nore all previous instructions").
_MARKUP_RE = re.compile(r"<[^<>]{0,64}>")
_ZERO_WIDTH_RE = re.compile(r"[​-‏‪-‮⁠﻿]")


def _normalise(text: str) -> str:
    """The forms an attacker can hide a directive behind, folded into one.

    Compatibility-normalise (so fullwidth and styled letters collapse to
    ASCII), drop zero-width and bidirectional controls, strip markup so a
    spliced directive re-assembles, and collapse runs of whitespace.
    """
    folded = unicodedata.normalize("NFKC", text)
    folded = _ZERO_WIDTH_RE.sub("", folded)
    folded = _MARKUP_RE.sub("", folded)
    return re.sub(r"\s+", " ", folded)


def screen_injection(text: str) -> Optional[str]:
    """Return a closed-set injection label, or None when the text is clean.

    Screens BOTH the raw text and the normalised form. The two catch different
    things and neither subsumes the other: a control token is visible raw and
    would be deleted by the markup strip, while a spliced directive is only
    visible once the markup is gone.
    """
    if not isinstance(text, str) or not text:
        return None
    candidates = (text, _normalise(text))
    for label, pattern in _INJECTION_PATTERNS:
        for candidate in candidates:
            if pattern.search(candidate):
                return label
    return None


def _credential_shaped(value: Any) -> bool:
    """True when the framework's own detector scores this value.

    The framework's detector is the floor, never a local approximation of it:
    a value it catches and this screen misses fails deeper in the pipeline,
    inside a gate whose error the caller cannot act on. ``detect_credentials_in_value``
    already recurses dicts and lists, and over a dict it is defined as the union
    over its values — so scanning field by field blocks exactly the same set
    while letting the refusal name the field.
    """
    return bool(detect_credentials_in_value(value))


def _safe_field_name(name: Any) -> str:
    """Echo a field name only when the name is itself inert.

    A field name is caller data. One that is not inert — or that trips a
    credential pattern on its own — is reported positionally instead.
    """
    if isinstance(name, str) and _INERT_RE.match(name) and not _credential_shaped(name):
        return name
    return "<unnamed field>"


def _check_inert(value: Any, field: str, refusals: List[Tuple[str, str]]) -> Optional[str]:
    if not isinstance(value, str):
        refusals.append((REASON_WRONG_TYPE, field))
        return None
    if _credential_shaped(value):
        refusals.append((REASON_CREDENTIAL_SHAPED, field))
        return None
    if not _INERT_RE.match(value):
        refusals.append((REASON_NOT_INERT, field))
        return None
    return value


def _check_enum(value: Any, allowed: Tuple[str, ...], field: str, refusals: List[Tuple[str, str]]) -> Optional[str]:
    if not isinstance(value, str):
        refusals.append((REASON_WRONG_TYPE, field))
        return None
    if value not in allowed:
        refusals.append((REASON_NOT_IN_ENUM, field))
        return None
    return value


def _check_bool(value: Any, field: str, refusals: List[Tuple[str, str]]) -> Optional[bool]:
    if not isinstance(value, bool):
        refusals.append((REASON_WRONG_TYPE, field))
        return None
    return value


def _check_int(value: Any, low: int, high: int, field: str, refusals: List[Tuple[str, str]]) -> Optional[int]:
    """A caller-controlled number, fail-CLOSED.

    ``float()`` parses "NaN" and "Infinity" and JSON admits them as bare
    literals; every comparison against NaN is False, so a NaN that reaches an
    arithmetic path silently produces a number no rule ever rejects. Bools are
    rejected explicitly — ``isinstance(True, int)`` is True in Python, so
    ``store_count: true`` would otherwise multiply as 1.
    """
    if isinstance(value, bool) or value is None:
        refusals.append((REASON_NOT_FINITE, field))
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        refusals.append((REASON_NOT_FINITE, field))
        return None
    # NaN fails both comparisons; +/-Infinity fails one. Neither can pass.
    if not (low <= number <= high) or number != int(number):
        refusals.append((REASON_NOT_FINITE, field))
        return None
    return int(number)


def _validate_record(raw: Any, index: int, refusals: List[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    where = f"catalog_records[{index}]"
    if not isinstance(raw, dict):
        refusals.append((REASON_WRONG_TYPE, where))
        return None
    for key in raw:
        if key not in _RECORD_FIELDS:
            refusals.append((REASON_UNKNOWN_FIELD, f"{where}.{_safe_field_name(key)}"))
            return None

    record: Dict[str, Any] = {}
    track_id = _check_inert(raw.get("track_id"), f"{where}.track_id", refusals)
    licensor = _check_enum(raw.get("licensor"), LICENSORS, f"{where}.licensor", refusals)
    license_type = _check_enum(raw.get("license_type"), LICENSE_TYPES, f"{where}.license_type", refusals)
    commercial_use = _check_bool(raw.get("commercial_use"), f"{where}.commercial_use", refusals)
    ai_generated = _check_bool(raw.get("ai_generated"), f"{where}.ai_generated", refusals)
    if refusals:
        return None

    record["track_id"] = track_id
    record["licensor"] = licensor
    record["license_type"] = license_type
    record["commercial_use"] = commercial_use
    record["ai_generated"] = ai_generated
    return record


def _validate_profile(raw: Any, refusals: List[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    limits = catalog_limits()
    if not isinstance(raw, dict):
        refusals.append((REASON_WRONG_TYPE, "usage_profile"))
        return None
    for key in raw:
        if key not in _PROFILE_FIELDS:
            refusals.append((REASON_UNKNOWN_FIELD, f"usage_profile.{_safe_field_name(key)}"))
            return None

    store_count = _check_int(
        raw.get("store_count", 1), 1, limits["max_store_count"], "usage_profile.store_count", refusals
    )
    monthly = _check_int(
        raw.get("monthly_performances", 0),
        0,
        limits["max_monthly_performances"],
        "usage_profile.monthly_performances",
        refusals,
    )
    channel = _check_enum(raw.get("channel", "physical_retail"), CHANNELS, "usage_profile.channel", refusals)
    if refusals:
        return None
    return {"store_count": store_count, "monthly_performances": monthly, "channel": channel}


def validate_context(raw: Any) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    """Reduce a caller context to the supported, inert subset.

    Returns ``(accepted, refusals)``. ``refusals`` holds ``(reason, field)``
    pairs drawn entirely from the closed sets above — no caller value is ever
    carried in either position.

    The post-condition the callers rely on:
    ``detect_credentials_in_value(accepted) == []``, always. That identity is
    what makes this screen exactly as wide as the framework's own gate rather
    than a local approximation that could drift narrower.
    """
    refusals: List[Tuple[str, str]] = []
    if raw is None:
        return {}, refusals
    if not isinstance(raw, dict):
        refusals.append((REASON_NOT_A_MAPPING, "input_context"))
        return {}, refusals
    raw = {k: v for k, v in raw.items() if k not in PLATFORM_RESERVED_KEYS}
    if not raw:
        return {}, refusals

    # Size first: a bound that is only applied after parsing is not a bound.
    if len(repr(raw)) > _MAX_CONTEXT_BYTES:
        refusals.append((REASON_TOO_LARGE, "input_context"))
        return {}, refusals

    for key in raw:
        if key not in _CONTEXT_FIELDS:
            refusals.append((REASON_UNKNOWN_FIELD, _safe_field_name(key)))
            return {}, refusals

    accepted: Dict[str, Any] = {}
    limits = catalog_limits()

    if "catalog_records" in raw:
        records = raw["catalog_records"]
        if not isinstance(records, list):
            refusals.append((REASON_WRONG_TYPE, "catalog_records"))
            return {}, refusals
        if len(records) > limits["max_records"]:
            refusals.append((REASON_TOO_MANY_RECORDS, "catalog_records"))
            return {}, refusals
        validated: List[Dict[str, Any]] = []
        for index, entry in enumerate(records):
            record = _validate_record(entry, index, refusals)
            if record is None:
                return {}, refusals
            validated.append(record)
        accepted["catalog_records"] = validated

    if "usage_profile" in raw:
        profile = _validate_profile(raw["usage_profile"], refusals)
        if profile is None:
            return {}, refusals
        accepted["usage_profile"] = profile

    return accepted, refusals


def default_profile() -> Dict[str, Any]:
    """The usage profile assumed when the caller supplies none.

    One store, no per-performance usage declared, physical retail — the
    smallest honest reading of "a shop asked about its background music".
    """
    return {"store_count": 1, "monthly_performances": 0, "channel": "physical_retail"}
