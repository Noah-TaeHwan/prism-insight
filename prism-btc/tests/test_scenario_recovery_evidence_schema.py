"""Recovery citations must select actual supplied paths, not invent aliases."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live.scenario_contract import _matches, response_schema, validate_wire_proposal
from live.scenario_llm import propose
from live.scenario_preview import response_contract
from tests.test_scenario_calculated_review import context, run, wire


PATH = "timeframes.30m.ma_path.forming_point.ma10"
INVENTED = "timeframes.30m.forming.ma_path.forming_point.ma10"


def recovery_context():
    ctx = context()
    ctx.update(recovery_contract_version=2, recovery=dict(
        phase="OBSERVING", first_observation=False, eligible=True,
        changed_evidence={PATH: {"before": .1, "now": .2}}))
    return ctx


def evidence_schema(schema):
    return schema["properties"]["recovery"]["anyOf"][0]["properties"]["changed_evidence"]


@pytest.mark.parametrize("version", [1, 2])
def test_schema_selects_exact_current_paths_without_mutating_context(version):
    ctx = recovery_context()
    ctx["recovery_contract_version"] = version
    before = deepcopy(ctx)
    schema = evidence_schema(response_schema(ctx))
    assert schema["items"]["enum"] == [PATH]
    assert _matches([PATH], schema)
    assert not _matches([INVENTED], schema)
    assert not _matches([PATH, "unknown"], schema)
    assert not _matches([PATH] * 9, schema)
    assert _matches([], schema)
    assert ctx == before


@pytest.mark.parametrize("recovery", [None, {}, {"changed_evidence": {}},
                                    {"changed_evidence": []}])
def test_no_supplied_evidence_only_allows_empty_citations(recovery):
    ctx = recovery_context()
    ctx["recovery"] = recovery
    schema = evidence_schema(response_schema(ctx))
    assert schema["maxItems"] == 0
    assert "enum" not in schema["items"]  # JSON Schema disallows an empty enum.
    assert _matches([], schema)
    assert not _matches([PATH], schema)


def test_no_recovery_contract_keeps_original_schema():
    assert "recovery" not in response_schema(context())["properties"]


def test_single_pass_request_contains_enum_and_preserves_valid_citation():
    ctx = recovery_context()
    candidate = wire(ctx, "LONG")
    candidate["recovery"] = dict(decision="PROBE", reason="raw", changed_evidence=[PATH],
                                 counterevidence="raw counter", invalidation="raw invalidation")
    calls = []

    def generate(**request):
        calls.append(request)
        return SimpleNamespace(text=json.dumps(candidate))

    result = propose(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
                     generate=generate, clock=lambda: 1000)
    schema = calls[0]["response_schema"]
    assert evidence_schema(schema)["items"]["enum"] == [PATH]
    assert result["recovery"] == candidate["recovery"]
    assert _matches(candidate, schema)
    assert not _matches(dict(candidate, invented_field=True), schema)
    malformed = deepcopy(candidate)
    malformed["recovery"]["invented_field"] = True
    assert not _matches(malformed, schema)


def test_raw_invalid_citations_are_not_rewritten_by_wire_normalization():
    ctx = recovery_context()
    raw = wire(ctx, "LONG")
    raw["recovery"] = dict(decision="PROBE", reason="raw", changed_evidence=[INVENTED],
                           counterevidence="raw counter", invalidation="raw invalidation")
    before = deepcopy(raw)
    assert validate_wire_proposal(raw, ctx)["recovery"] == before["recovery"]
    assert raw == before  # The existing recovery.assess authority still rejects it.


@pytest.mark.parametrize("path", [PATH, INVENTED])
def test_calculated_draft_inherits_schema_and_rejects_unknown_path(path):
    ctx = recovery_context()
    candidate = wire(ctx, "LONG")
    candidate["recovery"] = dict(decision="PROBE", reason="raw", changed_evidence=[path],
                                 counterevidence="raw counter", invalidation="raw invalidation")
    if path == INVENTED:
        with pytest.raises(ValueError, match="research_response_contract"):
            run(ctx, [candidate])
        return
    _, _, calls = run(ctx, [candidate])
    draft = calls[0]["response_schema"]["properties"]
    assert evidence_schema(draft["wait_proposal"])["items"]["enum"] == [PATH]
    assert evidence_schema(draft["candidates"]["items"])["items"]["enum"] == [PATH]
