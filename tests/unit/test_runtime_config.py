# RET-C2-335 — the declared runtime values reach the code that reads them.
#
# The failure this guards against is silent by construction. A reader pointed at
# the wrong file, or a graph constructed with no config, does not raise: it
# finds nothing, substitutes a default, and every value in config/config.yaml is
# dead while the suite stays green and the deployment looks configured.

import pytest

from src.services import runtime_config
from src.services.runtime_config import (
    FALLBACK_AGENT,
    FALLBACK_CATALOG,
    _CONFIG_PATH,
    _finite_in_range,
    agent_config,
    catalog_limits,
    load_runtime_config,
)


class TestTheShippedFile:
    def test_the_config_file_exists_where_the_loader_looks(self):
        assert _CONFIG_PATH.exists(), f"{_CONFIG_PATH} is missing"

    def test_the_shipped_file_parses_and_declares_the_documented_keys(self):
        loaded = load_runtime_config()
        assert loaded, "config/config.yaml parsed to an empty mapping"
        for key in ("max_retry", "timeout_s", "catalog"):
            assert key in loaded, key

    def test_every_declared_key_has_a_reader(self):
        """A declaration nothing reads is a promise the agent does not keep."""
        declared = set(load_runtime_config())
        resolved = set(agent_config())
        assert declared <= resolved, declared - resolved

    def test_the_fallbacks_mirror_the_shipped_file(self):
        """An unreadable config must degrade to the documented behaviour."""
        loaded = load_runtime_config()
        assert loaded["max_retry"] == FALLBACK_AGENT["max_retry"]
        assert loaded["timeout_s"] == FALLBACK_AGENT["timeout_s"]
        assert loaded["catalog"]["max_records"] == FALLBACK_CATALOG["max_records"]


class TestBoundsChecking:
    @pytest.mark.parametrize(
        "value",
        [
            "NaN",
            "Infinity",
            "-Infinity",
            float("nan"),
            float("inf"),
            None,
            True,
            "nonsense",
            -1,
            99,
        ],
    )
    def test_out_of_range_and_non_finite_values_are_rejected(self, value):
        assert _finite_in_range(value, 0, 9) is None

    @pytest.mark.parametrize("value", [0, 3, 9, "5", 5.0])
    def test_in_range_values_are_accepted(self, value):
        assert _finite_in_range(value, 0, 9) == float(value)

    def test_an_out_of_range_declaration_degrades_to_the_default(self, monkeypatch):
        monkeypatch.setattr(
            runtime_config,
            "load_runtime_config",
            lambda: {"max_retry": 99, "timeout_s": "NaN", "catalog": {"max_records": float("inf")}},
        )
        resolved = agent_config()
        assert resolved["max_retry"] == FALLBACK_AGENT["max_retry"]
        assert resolved["timeout_s"] == FALLBACK_AGENT["timeout_s"]
        assert resolved["catalog"]["max_records"] == FALLBACK_CATALOG["max_records"]

    def test_an_unreadable_config_degrades_rather_than_emptying(self, monkeypatch):
        monkeypatch.setattr(runtime_config, "load_runtime_config", lambda: {})
        assert agent_config()["max_retry"] == FALLBACK_AGENT["max_retry"]
        assert catalog_limits() == {k: int(v) for k, v in FALLBACK_CATALOG.items()}


class TestTheValuesReachTheGraph:
    def test_the_graph_is_constructed_with_the_declared_values(self):
        """Proved on the object the entry point actually built.

        Reading the file again here would prove only that the file is
        readable. The agent module's own graph is the thing a request runs
        against.
        """
        from src.api.server import agent

        assert agent.config["max_retry"] == load_runtime_config()["max_retry"]
        assert agent.config["timeout_s"] == load_runtime_config()["timeout_s"]

    def test_max_retry_is_the_value_the_backbone_route_reads(self):
        """The framework's route reads `max_retry` off the graph config.

        A graph built with no config would fall back to the framework's own
        default and the declared value would never be consulted.
        """
        from src.api.server import agent

        assert agent.config.get("max_retry") == 3

    def test_the_catalog_caps_reach_the_contract(self):
        from src.services.input_guard import validate_context

        cap = catalog_limits()["max_records"]
        records = [
            {
                "track_id": f"t{i}",
                "licensor": "jasrac",
                "license_type": "blanket",
                "commercial_use": True,
                "ai_generated": False,
            }
            for i in range(cap + 1)
        ]
        _accepted, refusals = validate_context({"catalog_records": records})
        assert refusals and refusals[0][0] == "too_many_records"

        _accepted, refusals = validate_context({"catalog_records": records[:cap]})
        assert refusals == []
