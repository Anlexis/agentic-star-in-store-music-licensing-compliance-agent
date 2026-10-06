# In-Store Music Licensing Compliance Agent

AI agent for checking in-store background music licensing and compliance, built with Agentic Star.

> **Category**: Cat 2 (domain-specific pipeline)
> **Industry**: Retail
> **Template ID**: RET-C2-335

## Overview

An agent for retailers who play recorded music on their premises. A shop or chain asks, in plain
language, whether the music it plays is licensed and what that costs — and sends along the
tracks in scope and how it uses them. The agent answers with a report: the licensing status of
each track, the charges for the year, and the policy position that applies when some of the music
was machine generated rather than written by a person.

Two things shape it.

The answer is computed from the request, not from a fixed table. The catalog records and the
usage profile — how many stores, how many performances a month, physical premises or a digital
channel — travel with the request, and the charges are the published rates multiplied by those
quantities. A single shop and a thousand-store chain get different numbers, and all four
licensing outcomes (covered, partly covered, not covered, unknown) come out of real record data.

The report's own promise is checked before it is released. It states that every monetary figure
is a published rate times a declared quantity, and therefore a whole multiple of the rate unit;
the output boundary re-derives that from the rendered text and withholds the report if it does
not hold. The same boundary refuses to release anything credential-shaped, using the framework's
own detector as its floor rather than a narrower list of its own.

A request that carries no catalog of its own is still answered, against a small shipped sample,
so the template runs end to end out of the box. The report says which of the two it used.
Replacing that sample with your own repertoire is the main thing a fork changes.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

The rates, the policy statements and the sample catalog in this repository are illustrative. They
are not the published schedule of any licensing body, and this template is not a substitute for
confirmation from one.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from the platform's own package registry, not the public index — which is why it is not listed as a dependency here. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded mode.
If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile or start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Calling it

`POST /invoke` takes the question and, optionally, the request context:

```json
{
  "input": "Is the background music we play in store covered by a blanket licence, and what does it cost for the year?",
  "input_context": {
    "catalog_records": [
      {
        "track_id": "shop_playlist_a",
        "licensor": "jasrac",
        "license_type": "blanket",
        "commercial_use": true,
        "ai_generated": false
      }
    ],
    "usage_profile": {
      "store_count": 12,
      "monthly_performances": 0,
      "channel": "physical_retail"
    }
  }
}
```

Every context field is checked against an explicit bound — an inert identifier alphabet, a closed
enum, or a finite numeric range — and an unsupported key is refused rather than ignored. A
refusal is a `400` naming the field; the value is never echoed back. `src/services/input_guard.py`
is the whole contract, and `docs/02_design.md` tabulates it.

When `INVOKE_AUTH_TOKEN` is set on the server environment, a caller that no upstream vouched for
must present it as a bearer token.

## Project Structure

```
src/          agent implementation (nodes, services, schemas, graph)
tests/        unit, integration and boundary tests
config/       agent.yaml (identity) and config.yaml (runtime values)
deploy/       local run recipe and the sign-off request payload
docs/         design and operational documentation
```

See `docs/` for the design and the test specification.

## Customising

1. Replace the sample catalog in `src/nodes/jasrac_nexttone_search_node.py` with a lookup against
   your own repertoire, or rely entirely on the caller-supplied records.
2. Replace the rates in `src/nodes/license_fee_calculate_node.py` with the schedule your
   licensing bodies publish. Keep them whole multiples of `FEE_UNIT_JPY`, or change that constant
   — the output boundary enforces whatever it says.
3. Replace the policy statements in `src/nodes/ai_bgm_policy_check_node.py`.
4. Adjust the bounds in `config/config.yaml` for your own request sizes.
5. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
