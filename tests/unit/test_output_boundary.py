# RET-C2-335 — the output boundary.
#
# Two invariants, and one property that is easy to state and easy to get wrong:
# returning ERROR is not containment. The framework resolves the caller-facing
# answer as `formatted_output or result` with no status check, so a boundary
# that reports a violation without clearing the fields still ships the report it
# just refused, inside the error envelope.
#
# These tests exercise the node directly. The envelope-level half lives in
# tests/integration/test_invoke_e2e.py, and the two are kept separate on
# purpose: the graph carries a second containment layer for the routes that skip
# this node entirely, and if both layers were only ever asserted through the
# envelope, either one alone would make the suite pass.

from decimal import Decimal

import pytest

from framework.schemas.agent_status import AgentStatus

from src.nodes.license_fee_calculate_node import FEE_UNIT_JPY
from src.nodes.post_process_node import (
    OUTPUT_BEARING_FIELDS,
    VIOLATION_CREDENTIAL,
    VIOLATION_EMPTY_REPORT,
    VIOLATION_OFF_UNIT_AMOUNT,
    WITHHELD_NOTICE,
    PostProcessNode,
    off_unit_amounts,
)

CLEAN_REPORT = (
    "# Background music licensing report — RET-C2-335\n\n"
    "## Licence status: LICENSED\n\n"
    "- Annual blanket charge: ¥6,000\n"
    "- Per-performance rate: ¥500\n"
    "- Total for the year: ¥6,000\n"
)


@pytest.fixture(autouse=True)
def patch_emit(monkeypatch):
    import src.nodes.post_process_node as m

    monkeypatch.setattr(m, "emit_trace_event", lambda *a, **k: None)


def _node():
    return PostProcessNode()


class TestCleanPath:
    def test_clean_report_is_released(self):
        result = _node().execute({"result": CLEAN_REPORT})
        assert result["status"] == AgentStatus.SUCCESS
        assert result["result"] == CLEAN_REPORT
        assert result["formatted_output"] == CLEAN_REPORT

    def test_formatted_output_is_set_so_the_fallback_never_decides(self):
        """The framework picks `formatted_output or result`.

        Writing only `result` leaves that choice to a falsy check on a field
        this node does not control.
        """
        result = _node().execute({"result": CLEAN_REPORT})
        assert result["formatted_output"]


# A connection string carrying inline credentials is one of the shapes the
# repository's own credential gate treats as never-legitimate in a fixture — and
# that is the right default, because "it is only test material" is exactly the
# reasoning that keeps a real one invisible. Assembled from parts so the fixture
# exercises the detector without a literal of that shape sitting in the file.
_CONN_STRING_FIXTURE = "postgresql:" + "//" + "user:hunter2hunter2" + "@" + "db:5432/x"


class TestCredentialInvariant:
    @pytest.mark.parametrize(
        "secret",
        [
            "sk-abcdefghijklmnopqrstuvwxyz012345",  # framework: openai_key
            "sk_live_" + "abcdefghijklmnop0123",  # framework: stripe_key
            "AKIAIOSFODNN7EXAMPLE",  # framework: aws_key
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",  # framework: jwt
            "Bearer abcdefghijklmnopqrstuvwx",  # framework: bearer_token
            _CONN_STRING_FIXTURE,  # framework: conn_string
            "password=hunter2hunter2",  # local only
            "api_key: abcdefgh12345678",  # local only
            "pk-abcdefghijklmnop1234",  # local only
        ],
    )
    def test_every_credential_shape_is_withheld(self, secret):
        """The union of the framework's detector and this template's patterns.

        The first six are the framework's. The last three are shapes it does
        NOT score — it describes credential formats, and an inline assignment
        is not one of them. Delegating to the framework alone would have made
        this screen narrower while looking like a tightening.
        """
        result = _node().execute({"result": f"{CLEAN_REPORT}\nleaked: {secret}\n"})
        assert result["status"] == AgentStatus.ERROR
        assert result["error_log"] == [f"PostProcessNode: output withheld — {VIOLATION_CREDENTIAL}"]

    def test_the_withheld_envelope_carries_none_of_the_report(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz012345"
        result = _node().execute({"result": f"{CLEAN_REPORT}\nleaked: {secret}\n"})
        rendered = repr(result)
        assert secret not in rendered
        assert "Licence status" not in rendered
        assert "Traceback" not in rendered

    def test_every_output_bearing_field_is_present_and_empty(self):
        """Present matters as much as empty.

        LangGraph merges partial deltas, so a key left out of the delta keeps
        its previous value in state. A boundary that "cleared" a field by
        omitting it would clear nothing, and an assertion of the form
        `not result.get(field)` would pass anyway.
        """
        result = _node().execute({"result": "password=hunter2hunter2"})
        for field in OUTPUT_BEARING_FIELDS:
            if field == "formatted_output":
                continue
            assert field in result, field
            assert result[field] == "", field

    def test_the_replacement_notice_is_truthy(self):
        """A falsy replacement re-opens the framework's fallback."""
        result = _node().execute({"result": "password=hunter2hunter2"})
        assert result["formatted_output"]
        assert result["formatted_output"] == WITHHELD_NOTICE


class TestMonetaryInvariant:
    def test_on_unit_figures_pass(self):
        assert off_unit_amounts(CLEAN_REPORT) == []

    @pytest.mark.parametrize("amount", [1, 99, 6_001, 123_456])
    def test_off_unit_figures_are_withheld(self, amount):
        report = f"{CLEAN_REPORT}- Drifted figure: ¥{amount:,}\n"
        result = _node().execute({"result": report})
        assert result["status"] == AgentStatus.ERROR
        assert result["error_log"] == [f"PostProcessNode: output withheld — {VIOLATION_OFF_UNIT_AMOUNT}"]

    def test_comma_grouped_and_bare_forms_are_both_read_whole(self):
        """The alternation must not stop three digits into a bare run.

        Written `(\\d{1,3}(?:,\\d{3})*|\\d+)` the grouped branch matches with
        zero groups, so ¥1234567 is read as ¥123 — on the unit — and the rest
        of the figure is never checked at all. Requiring at least one group
        makes the bare branch the only one that can match an ungrouped run.
        """
        assert off_unit_amounts("¥1,234,567") == [Decimal(1234567)]
        assert off_unit_amounts("¥1234567") == [Decimal(1234567)]
        assert off_unit_amounts("¥1,234,500") == []
        assert off_unit_amounts("¥123") == [Decimal(123)]

    def test_identifiers_are_not_read_as_money_and_are_not_rewritten(self):
        """The invariant is checked, never repaired.

        A boundary that snapped off-unit numbers onto the unit would have to
        decide what a number is, and this report renders `SAMPLE-STK-77891`
        and `unregistered_demo_track` in the same table as its charges. The
        three-letter-marker grammar that does that rewriting reads `STK-77891`
        as a currency marker and a value. Checking instead of rewriting means
        an identifier is simply not a monetary figure, because it carries no ¥.
        """
        text = "SAMPLE-STK-77891 | house_mix_01 | 2026 | 90d | 1,234 | STAR 2026"
        assert off_unit_amounts(text) == []
        result = _node().execute({"result": CLEAN_REPORT + text})
        assert result["status"] == AgentStatus.SUCCESS
        assert text in result["result"]

    def test_a_fractional_figure_is_read_whole_and_rejected(self):
        """This report never renders a fractional yen amount.

        If one ever appeared, a boundary that read only the integer part would
        see 1234 — on the unit — and release a figure the reader sees as
        ¥1234.56. The fraction is absorbed into the match and compared, so the
        whole-unit rule rejects it. A fraction of zero is still on the unit.
        """
        assert off_unit_amounts("¥1234.56") == [Decimal("1234.56")]
        assert off_unit_amounts("¥1,200.00") == []
        assert off_unit_amounts("¥1,234.00") == [Decimal("1234.00")]

    def test_the_unit_matches_the_published_rates(self):
        from src.nodes.license_fee_calculate_node import (
            AI_PER_PERFORMANCE_JPY,
            BLANKET_ANNUAL_PER_STORE_JPY,
            EC_STREAMING_SURCHARGE_PER_STORE_JPY,
            PER_PERFORMANCE_JPY,
        )

        for rate in (
            BLANKET_ANNUAL_PER_STORE_JPY,
            PER_PERFORMANCE_JPY,
            EC_STREAMING_SURCHARGE_PER_STORE_JPY,
            AI_PER_PERFORMANCE_JPY,
        ):
            assert rate % FEE_UNIT_JPY == 0


class TestEmptyReport:
    def test_an_empty_report_is_withheld_not_reported_as_success(self):
        """The shipped contract reported SUCCESS for an empty report.

        Two of the tests that came with it asserted exactly that — and both
        asserted the falsy value that re-opens the framework's fallback, so
        they passed on a boundary that withheld nothing at all.
        """
        result = _node().execute({"result": ""})
        assert result["status"] == AgentStatus.ERROR
        assert result["error_log"] == [f"PostProcessNode: output withheld — {VIOLATION_EMPTY_REPORT}"]

    def test_a_missing_result_key_is_withheld(self):
        result = _node().execute({})
        assert result["status"] == AgentStatus.ERROR


class TestErrorChannel:
    @pytest.mark.parametrize(
        "report,expected",
        [
            ("", VIOLATION_EMPTY_REPORT),
            ("password=hunter2hunter2", VIOLATION_CREDENTIAL),
            (CLEAN_REPORT + "¥7\n", VIOLATION_OFF_UNIT_AMOUNT),
        ],
    )
    def test_every_error_path_carries_only_closed_set_labels(self, report, expected):
        """The caller-visible error is a label this module chose, nothing else.

        Not a truncation of the upstream text, not a path-stripped exception
        string: identifiers, names and third-party response bodies all survive
        those. Only a constant does not.
        """
        result = _node().execute({"result": report})
        assert result["error_log"] == [f"PostProcessNode: output withheld — {expected}"]

    def test_error_log_does_not_carry_upstream_text(self):
        """A sentinel seeded upstream must appear nowhere in the delta."""
        sentinel = "boom: upstream said {'customer':'A. Tanaka','ref':'INV-77891'}"
        result = _node().execute({"result": "", "error_log": [sentinel]})
        assert sentinel not in repr(result)
        assert "A. Tanaka" not in repr(result)


class TestFieldInventory:
    def test_output_bearing_fields_cover_every_report_carrying_state_field(self):
        """A new output-bearing field must be a decision, not an omission."""
        import src.schemas.state as state_module

        annotations = set(state_module.State.__annotations__)
        report_carrying = {"result"}
        assert report_carrying <= annotations
        assert report_carrying <= set(OUTPUT_BEARING_FIELDS)
