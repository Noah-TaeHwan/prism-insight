"""Exact recovery change references; no market/model/broker calls."""
from copy import deepcopy
import hashlib
import json

import pytest

from live.scenario_model_input import project_model_input, restore_model_input


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def payload():
    baseline = {f"timeframes.30m.confirmed.path_{i}": 81000.12345678912 + i for i in range(60)}
    current = {key: value + 100.125 for key, value in baseline.items()}
    changes = {key: dict(before=baseline[key], now=value) for key, value in current.items()}
    return dict(market_snapshot={}, contract_context=dict(recovery=dict(
        baseline=baseline, current=current, changed_evidence=changes)), response_contract=dict(required_fact=True))


def test_exact_restore_has_host_computed_changed_keys_and_no_source_mutation():
    source = payload()
    before = deepcopy(source)
    result, audit = project_model_input(source)
    assert result["model_input_projection"]["version"] == 5
    reference = result["contract_context"]["recovery"]["changed_evidence"]
    assert reference["path_prefix"] == "timeframes."
    assert [reference.get("path_prefix", "") + key for key in reference["key_order"]] == list(
        source["contract_context"]["recovery"]["changed_evidence"])
    assert "Do NOT calculate which paths changed" in result["model_input_projection"]["meaning"]
    assert encoded(restore_model_input(result)) == encoded(source)
    assert source == before
    assert audit["projected_utf8_bytes"] < audit["original_utf8_bytes"]
    assert audit["projected_sha256"] == hashlib.sha256(encoded(result)).hexdigest()
    assert result["response_contract"] == source["response_contract"]


def test_reordered_changed_paths_restore_original_order():
    source = payload()
    recovery = source["contract_context"]["recovery"]
    recovery["changed_evidence"] = dict(reversed(list(recovery["changed_evidence"].items())))
    result, _ = project_model_input(source)
    assert encoded(restore_model_input(result)) == encoded(source)


@pytest.mark.parametrize("fault", ["missing", "wrong_before", "wrong_now", "extra", "float_to_int", "bool_to_int", "baseline_bool"])
def test_inexact_or_incomplete_changed_facts_are_not_replaced(fault):
    source = payload()
    recovery = source["contract_context"]["recovery"]
    key = next(iter(recovery["baseline"]))
    if fault == "missing":
        recovery["changed_evidence"].pop(key)
    elif fault == "extra":
        recovery["changed_evidence"]["extra"] = dict(before=1, now=2)
    elif fault == "baseline_bool":
        recovery["baseline"][key] = True
    elif fault in {"float_to_int", "bool_to_int"}:
        recovery["baseline"][key] = 1
        recovery["changed_evidence"][key]["before"] = 1.0 if fault == "float_to_int" else True
    else:
        recovery["changed_evidence"][key]["before" if fault == "wrong_before" else "now"] = 12
    result, _ = project_model_input(source)
    assert "$recovery_changes" not in result["contract_context"]["recovery"]["changed_evidence"]
    assert encoded(restore_model_input(result)) == encoded(source)


@pytest.mark.parametrize("fault", ["version", "bool_version", "old_version", "path", "missing_path", "marker_version",
    "extra_marker_field", "missing_keys", "duplicate_keys", "unknown_key", "key_type", "reordered_keys", "hash", "source_value", "source_type", "nested_marker", "prefix", "prefix_type", "missing_prefix"])
def test_malformed_or_tampered_reference_rejected(fault):
    result, _ = project_model_input(payload())
    metadata = result["model_input_projection"]
    recovery = result["contract_context"]["recovery"]
    reference = recovery["changed_evidence"]
    if fault == "version":
        metadata["version"] = 6
    elif fault == "bool_version":
        metadata["version"] = True
    elif fault == "old_version":
        metadata["version"] = 4
    elif fault == "path":
        metadata["recovery_changed_evidence_path"] = "contract_context.positions"
    elif fault == "missing_path":
        metadata.pop("recovery_changed_evidence_path")
    elif fault == "marker_version":
        reference["$recovery_changes"] = "baseline-current-v2"
    elif fault == "extra_marker_field":
        reference["extra"] = 1
    elif fault == "missing_keys":
        reference.pop("key_order")
    elif fault == "duplicate_keys":
        reference["key_order"].append(reference["key_order"][0])
    elif fault == "unknown_key":
        reference["key_order"][0] = "unknown"
    elif fault == "key_type":
        reference["key_order"][0] = 5
    elif fault == "reordered_keys":
        reference["key_order"].reverse()
    elif fault == "hash":
        reference["restored_sha256"] = "0" * 64
    elif fault in {"source_value", "source_type"}:
        key = reference.get("path_prefix", "") + reference["key_order"][0]
        recovery["baseline"][key] = 1 if fault == "source_value" else True
    elif fault == "prefix":
        reference["path_prefix"] = "account."
    elif fault == "prefix_type":
        reference["path_prefix"] = 1
    elif fault == "missing_prefix":
        reference.pop("path_prefix")
    else:
        result["extra"] = {"$recovery_changes": "baseline-current-v1"}
    with pytest.raises(ValueError):
        restore_model_input(result)


def test_unregistered_reference_and_double_projection_rejected():
    marker = dict(contract_context=dict(nested={"$recovery_changes": "baseline-current-v1"}))
    with pytest.raises(ValueError, match="reserved_projection_field"):
        project_model_input(marker)
    with pytest.raises(ValueError, match="unregistered_recovery_change"):
        restore_model_input(marker)
    result, _ = project_model_input(payload())
    with pytest.raises(ValueError, match="reserved_projection_field"):
        project_model_input(result)


def test_small_change_map_not_inflated_and_equal_int_float_unchanged():
    source = dict(contract_context=dict(recovery=dict(baseline=dict(a=1, b=2), current=dict(a=1.0, b=3),
        changed_evidence=dict(b=dict(before=2, now=3)))))
    result, _ = project_model_input(source)
    assert encoded(result) == encoded(source)


def test_v5_composes_with_market_current_and_table_references():
    from tests.test_scenario_model_input import payload as shaped_payload
    from live.scenario_recovery import numeric_market
    source = shaped_payload()
    current = numeric_market(source["market_snapshot"])
    baseline = {key: value + 100.125 for key, value in current.items()}
    source["contract_context"]["recovery"].update(baseline=baseline, current=current,
        changed_evidence={key: dict(before=baseline[key], now=value) for key, value in current.items()})
    result, _ = project_model_input(source)
    assert result["model_input_projection"]["version"] == 5
    assert result["model_input_projection"]["numeric_market_references"]
    assert encoded(restore_model_input(result)) == encoded(source)


def test_v5_composes_with_keyed_ma_tables_and_shared_candidate_receipts():
    from tests.test_scenario_proposal_projection import shared_payload
    source = shared_payload()
    source["contract_context"]["recovery"] = payload()["contract_context"]["recovery"]
    source["contract_context"]["ma_structure"] = dict(levels={f"level_{i}": dict(
        timeframe="4h", moving_average="ma35", precise_price=81000.12345678912 + i,
        distance_from_current_price=20.12345678912 + i) for i in range(20)})
    projected, audit = project_model_input(source)
    meta = projected["model_input_projection"]
    assert meta["version"] == 5
    assert "contract_context.ma_structure.levels" in meta["row_table_paths"]
    assert "keys" in projected["contract_context"]["ma_structure"]["levels"]["$row_table"]
    assert meta["receipt_value_paths"]
    assert meta["model_proposal_paths"]
    assert meta["model_wrapper_paths"]
    assert encoded(restore_model_input(projected)) == encoded(source)
    # Actual transport may sort dictionary keys; semantic restoration stays exact.
    sorted_wire = json.loads(json.dumps(projected, sort_keys=True))
    restored = restore_model_input(sorted_wire)
    assert json.dumps(restored, sort_keys=True) == json.dumps(source, sort_keys=True)
    assert audit["projected_utf8_bytes"] < audit["original_utf8_bytes"]
