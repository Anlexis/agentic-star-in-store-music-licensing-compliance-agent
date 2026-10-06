# Template Design Specification — RET-C2-335

## Position in AgentCore Architecture

| Field | Value |
|---|---|
| Agent class | RETMusicBGMLicensingSearchAgent |
| L1 Base (framework base class) | AgentBaseGraph — direct framework inheritance |
| Composition | Cat 2, two-layer nested (outer backbone + inner domain graph) |

- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution; outer + inner graphs)

## Architecture Overview

RET-C2-335 answers a retail operator's question about the background music played on its
premises: whether the music in scope is covered by a licence, what that costs for the year, and
what policy applies when some of it was machine generated.

A request carries two things. The question is plain language. The **catalog records and usage
profile** travel separately, on the request's structured context channel, and they are what the
answer is computed from — how many stores, how many performances a month, which channel, and
one record per track with its licensor, its licence type and whether it was machine generated.

A request that carries no records is still answered, against a small shipped sample catalog, so
the template runs end to end out of the box. The report says which of the two it used, because
an answer about the sample is not an answer about anybody's repertoire.

### Why the catalog travels on the context channel

Two reasons, and the second is not obvious.

The first is injection surface: records rendered into the report are locked to an inert
identifier alphabet, which a free-text field in the question could never be.

The second is that the framework's input gate masks personal-data shapes in `user_input` before
any node runs, and its personal-name heuristic matches any run of two or more title-case words.
For a music licensing agent that is the ordinary case — "Is Maurice Ravel's Bolero covered?"
reaches the agent as "[MASKED]'s Bolero covered?". The context channel is not scanned that way,
and identifiers are not names. The behaviour is pinned by a test
(`test_a_proper_noun_in_a_question_is_masked_by_the_platform`) rather than worked around,
because it is platform behaviour and a template cannot change it.

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | Framework bootstrap (session, schema, trust) | — | session_id, schema_version, trust_level | InitializeNode (default) |
| pre_process | S-1 input gate: bounds, injection screen, personal-data screen | user_input | validated_input, status | PreProcessNode (VERIFIED_EXTERNAL) |
| main | Cat 2 GraphNode: delegates to BGMLicensingWorkflowGraph | validated_input, input_context | result, license_status, fee_schedule | BGMLicensingGraphNode |
| post_process | S-3 output boundary: credential union scan + monetary invariant | result | formatted_output, result, status | PostProcessNode (ANONYMOUS) |
| finalize | Build response metadata | result | output, response_metadata | FinalizeNode (default) |

**Inner domain workflow (BGMLicensingWorkflowGraph)**:

| Node | Responsibility | Input | Output |
|------|---------------|-------|--------|
| query_sanitize | Screen the question, validate the caller context, normalise for lookup | user_input, input_context | sanitized_query, validated_context |
| jasrac_nexttone_search | Resolve the records in scope; derive licensing status | sanitized_query, validated_context | search_results, license_status, catalog_source |
| license_fee_calculate | Compute the charges from the usage profile | license_status, search_results, validated_context | fee_schedule |
| ai_bgm_policy_check | Select the policy statement the records engage | search_results | ai_bgm_policy |
| license_report_format | Assemble the report | all domain fields | result |

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                       ↓ (retry, max_retry from config/config.yaml)
                                   pre_process

Inside `main` (BGMLicensingGraphNode):
  Inner graph BGMLicensingWorkflowGraph:
    START → query_sanitize → jasrac_nexttone_search → license_fee_calculate
                                                             ↓
                                                   ai_bgm_policy_check → license_report_format → END
```

The framework's subgraph call is `invoke(user_input, session_id, ctx)` — it has no parameter for
the request's structured context. `src/graph/context_bridge.py` carries it across: the outer node
stashes it in a ContextVar as it extracts the question, and the inner graph seeds its own initial
state from the same variable through `_extra_initial_state()`. Without it the inner graph reads an
empty mapping on every request, answers from the shipped sample, and raises no error anywhere.

### State Definition

| Field | Type | Purpose | Written by |
|-------|------|---------|------------|
| validated_input | Optional[str] | The question, validated (outer layer) | PreProcessNode |
| sanitized_query | Optional[str] | The question, normalised for lookup | QuerySanitizeNode |
| validated_context | Optional[Dict] | The accepted, bounded subset of input_context | QuerySanitizeNode |
| search_results | Optional[List[Dict]] | The records in scope | JASRACNexToneSearchNode |
| catalog_source | Optional[str] | "caller" or "baseline_sample" | JASRACNexToneSearchNode |
| license_status | Optional[str] | licensed / partial / unlicensed / unknown | JASRACNexToneSearchNode |
| fee_schedule | Optional[Dict] | The computed charges | LicenseFeeCalculateNode |
| ai_bgm_policy | Optional[str] | The applicable policy statement | AIBGMPolicyCheckNode |
| result | Optional[str] | The assembled report | LicenseReportFormatNode |
| trace_id | Optional[str] | Framework tracing | framework-managed |
| correlation_id | Optional[str] | Correlation for audit trail | framework-managed |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## The caller-data contract

Enforced in `src/services/input_guard.py`, called both by the entry point (so a bad request is
refused with 400 before the graph runs) and by `QuerySanitizeNode` (so the guarantee holds when
the agent runs behind the gateway and the entry point is not in the path).

| Field | Rule |
|---|---|
| `catalog_records` | list, at most `catalog.max_records` entries |
| `catalog_records[].track_id` | inert identifier, `[a-z0-9_-]{1,32}` |
| `catalog_records[].licensor` | one of `jasrac`, `nextone`, `other` |
| `catalog_records[].license_type` | one of `blanket`, `per_performance`, `none` |
| `catalog_records[].commercial_use` | boolean |
| `catalog_records[].ai_generated` | boolean |
| `usage_profile.store_count` | finite integer, 1 … `catalog.max_store_count` |
| `usage_profile.monthly_performances` | finite integer, 0 … `catalog.max_monthly_performances` |
| `usage_profile.channel` | one of `physical_retail`, `ec_streaming`, `both` |

Anything else is **refused**, not ignored. An unsupported key stays on the context channel, the
framework's first node returns that channel verbatim in its own result, and the framework's
output scan then fails the whole run with an error the caller cannot act on.

Every number goes through a finite + bounded parser. `float()` parses "NaN" and "Infinity", JSON
admits them as bare literals, and every comparison against NaN is False — so a NaN reaching the
fee arithmetic produces a figure no range check rejects. Booleans are rejected explicitly:
`isinstance(True, int)` is True in Python, so `store_count: true` would multiply as one store.

A credential-shaped value is refused as well, and the inert alphabet does not make that
redundant: `sk-` plus twenty lower-case characters is a valid identifier under
`[a-z0-9_-]{1,32}` and is also a key the framework's detector scores. The screen asks the
framework's own detector, so it can never be narrower than the gate it is standing in front of.

Refusals name the **field**, never the value, and a field name is itself echoed only when it is
inert.

## The output invariant

The report states: *every monetary figure in this report is a published rate multiplied by a
declared quantity, and is therefore a whole multiple of ¥100.* The output boundary re-derives
that from the rendered report rather than trusting the arithmetic that produced it.

The invariant is **checked, never repaired**. A boundary that rewrote off-unit figures onto a
grid would have to decide what a number is, and this report renders `SAMPLE-STK-77891` and
`unregistered_demo_track` in the same table as its charges — a grammar that reads any standalone
three-letter uppercase token as a currency marker turns the first of those into a monetary value.
Rounding is also wrong on its own terms here: a ¥500 per-performance rate snapped to the nearest
¥1,000 is either ¥0 or ¥1,000, and neither is the published rate. Checking sidesteps both: an
identifier carries no ¥ and is therefore not a monetary figure at all.

Two details of the figure grammar are load-bearing and are regression-tested:
the comma-grouped alternative requires at least one group, or the alternation matches the first
three digits of a bare run and the rest of the figure is never checked; and the fractional part
is absorbed into the match, or ¥1234.56 is read as 1234 and passes.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, trust_level injected via backbone)
- [x] S-1: trust is enforced once, at `pre_process` (VERIFIED_EXTERNAL); inner nodes are ANONYMOUS
- [x] S-2: the framework's `@final` input gate runs automatically (personal-data masking +
      injection policy on `user_input` / `validated_input`). The template does **not** rely on it:
      `PreProcessNode.execute()` and `QuerySanitizeNode.execute()` own the refusal, and the unit
      tests call those methods directly with no framework wrapper in front.
- [x] S-3: the output boundary's scan lives in `PostProcessNode.execute()`, not in an
      `_extra_security_gate_output()` override — a violation must CLEAR the output-bearing state
      fields, and a hook that only raises cannot do that. The framework's `@final` credential scan
      still runs on everything the node returns.
- [x] S-4: `emit_trace_event()` on every path of every `execute()` (positional args, free-function
      form). Per-node events: `bgm_query_sanitized`, `bgm_context_rejected`,
      `jasrac_nexttone_search_complete`, `license_fee_calculated`, `ai_bgm_policy_checked`,
      `license_report_formatted`, `pre_process_validated`, `pre_process_rejected`,
      `post_process_complete`, `post_process_output_withheld`

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode` / `RemoteAgentNode` → deliberate no-op (upstream node's gate already applied)

### Composition Pattern

- **Pattern**: Cat 2 nested (outer AgentBaseGraph + inner BaseGraph via GraphNode in `main` slot)
- **Composition target**: BGMLicensingWorkflowGraph (inner BaseGraph)
- **Error propagation strategy**: propagate (SubgraphError on inner graph error)

## Containment

The framework resolves the caller-facing answer as `formatted_output or result`, with no status
check. Two layers follow from that, and they cover different routes:

| Layer | Where | Route it covers |
|---|---|---|
| Clear the output-bearing fields | `PostProcessNode._withhold()` | a violation found DURING the boundary check |
| Withhold on any non-success status | `RETMusicBGMLicensingSearchAgent.get_output()` | every route that SKIPS the boundary |

The second is not redundant. The backbone routes a non-success status straight to `finalize`, so
an input rejection at `pre_process` and a subgraph failure at `main` never reach the boundary at
all. Because each layer alone would satisfy an envelope-level assertion, the first is asserted at
the node level (`tests/unit/test_output_boundary.py`) and the second through the envelope
(`tests/integration/test_invoke_e2e.py`), so neither set of tests can be satisfied by the other.

Both replacements are truthy: a falsy `formatted_output` re-opens the framework's fallback onto
whatever survived in state.

## Runtime configuration

`config/agent.yaml` is the static manifest and holds no runtime values. `config/config.yaml`
holds `max_retry`, `timeout_s` and the caller-data bounds, and `src/services/runtime_config.py`
is the single reader. The entry point constructs the graph with those values; a graph built with
no config would leave every declared value unread while the framework substituted its own
defaults, with nothing failing.

## Security Gate Summary

| S-layer | Where | Implementation |
|---------|-------|---------------|
| S-1 (trust) | PreProcessNode (outer) | VERIFIED_EXTERNAL required; inner nodes ANONYMOUS |
| S-2 (input) | PreProcessNode, QuerySanitizeNode | bounds, injection screen (directive phrases + chat-template control tokens, raw and normalised), personal-data screen, caller-context contract |
| S-3 (output) | PostProcessNode | credential union scan (framework detector + two local patterns), monetary invariant, clear-on-violation |
| S-4 (audit) | All nodes | `emit_trace_event()` on every path of every `execute()` |
| S-5 (credential) | FunctionNode built-in `_security_gate_output()` (automatic) | credential scan on every value every node returns |

## Import Isolation Confirmation
- [x] Template does not import the platform SDK (Level 0)
- [x] Import targets: `framework.*` and `shared.*` only
- [x] `src.` prefix used for all agent-local imports

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed multi-step pipeline; no autonomous loop needed |
| Composition pattern | Cat 1 flat | Cat 2 nested GraphNode | Cat 2 nested | 5 domain steps require inner graph encapsulation |
| Inner graph parent | BaseGraph | AgentBaseGraph | BaseGraph | Custom topology (no standard pre/main/post slots needed) |
| Inner error strategy | propagate | handle | propagate | Fail-fast preferred; no graceful degradation requirement |
| Trust gate placement | pre_process (VERIFIED_EXTERNAL) | inner nodes | pre_process only | Inner nodes ANONYMOUS per CoE trust-trap rule (review finding 5) |
| Caller catalog channel | question text | structured context | structured context | Inert alphabet; and the framework's name masking would otherwise eat composer and performer names |
| Monetary invariant | snap to a grid | check against the rate unit | check | The report renders identifiers beside its charges; a rewriting grid corrupts them, and rounding a ¥500 rate destroys it |
| Injection handling | strip and continue | refuse | refuse | Stripping hides the attack and mangles ordinary licensing prose |
