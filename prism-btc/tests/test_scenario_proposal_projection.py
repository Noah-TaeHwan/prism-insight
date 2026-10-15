"""SELECT lossless projection: no model calls or operational state writes."""
from copy import deepcopy
import hashlib
import json

import pytest

from live.scenario_model_input import project_model_input, restore_model_input


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def payload(count=2):
    wait = {"action": "WAIT", "rationale": "관측 " * 300, "numeric_types": [True, 1, 1.0]}
    candidates = [{"action": "ADJUST", "rationale": "판단 " * 300, "allocation_fraction": .5, "index": i}
                  for i in range(count)]
    wrapper = {"wait_proposal": wait, "candidates": candidates}
    receipts = []
    for i, proposal in enumerate([wait, *candidates]):
        receipts.append(dict(id="WAIT" if i == 0 else f"c{i-1}", raw_model_wrapper=deepcopy(wrapper),
            raw_model_wrapper_sha256=digest(wrapper), raw_allocation_proposal=deepcopy(proposal),
            raw_proposal=deepcopy(proposal) if i == 0 else dict(proposal, quantity=.02)))
    return dict(contract_context={"positions": [{"quantity": .02}]}, market_snapshot={},
                calculated_candidates=dict(wait=receipts[0], candidates=receipts[1:]))


@pytest.mark.parametrize("count", [0, 1, 2])
def test_proposal_refs_restore_sorted_wire_exactly(count):
    source = payload(count)
    before = deepcopy(source)
    projected, audit = project_model_input(source)
    assert source == before
    assert audit["version"] == 4
    restored = restore_model_input(json.loads(encode(projected)))
    assert encode(restored) == encode(source)
    assert digest(restored) == audit["original_semantic_sha256"]
    assert audit["projected_utf8_bytes"] < audit["original_utf8_bytes"]
    for receipt in projected["calculated_candidates"]["candidates"]:
        assert receipt["raw_proposal"]["overrides"]["quantity"] == .02


@pytest.mark.parametrize("fault", ["unknown", "missing", "duplicate", "bad_path", "wrapper_hash", "legacy_version"])
def test_proposal_refs_fail_closed(fault):
    projected, _ = project_model_input(payload(0))
    meta = projected["model_input_projection"]
    receipt = projected["calculated_candidates"]["wait"]
    if fault == "unknown":
        receipt["raw_proposal"]["$model_proposal"] = "unknown"
    elif fault == "missing":
        meta.pop("model_proposal_paths")
    elif fault == "duplicate":
        meta["model_proposal_paths"].append(meta["model_proposal_paths"][0])
    elif fault == "bad_path":
        meta["model_proposal_paths"][0] = "contract_context.positions"
    elif fault == "wrapper_hash":
        receipt["raw_model_wrapper_sha256"] = "bad"
    else:
        meta["version"] = 1
    with pytest.raises(ValueError):
        restore_model_input(projected)


def test_reserved_proposal_marker():
    for fn in (project_model_input, restore_model_input):
        with pytest.raises(ValueError):
            fn({"unexpected": {"$model_proposal": "unknown"}})


def test_keyed_tables_sorted_wire_and_tampering():
    rows = {str(i): {"long_column_name_one": i, "long_column_name_two": 1.0,
                     "long_column_name_three": True} for i in range(40)}
    source = {"contract_context": {"ma_structure": {"levels": rows},
                                   "recovery": {"changed_evidence": deepcopy(rows)}}}
    projected, audit = project_model_input(source)
    assert audit["version"] == 4
    assert restore_model_input(json.loads(encode(projected))) == source
    for fault in ("duplicate", "missing", "missing_keys", "wrong_version"):
        bad = deepcopy(projected)
        keys = bad["contract_context"]["ma_structure"]["levels"]["$row_table"]["keys"]
        if fault == "duplicate":
            keys[0] = keys[1]
        elif fault == "missing":
            keys.pop()
        elif fault == "missing_keys":
            del bad["contract_context"]["ma_structure"]["levels"]["$row_table"]["keys"]
        else:
            bad["model_input_projection"]["version"] = 2
        with pytest.raises(ValueError):
            restore_model_input(bad)


@pytest.mark.parametrize("fault", ["override", "remove", "hash", "receipt_hash"])
def test_proposal_delta_tampering(fault):
    projected, _ = project_model_input(payload())
    receipt = projected["calculated_candidates"]["candidates"][0]
    ref = receipt["raw_proposal"]
    if fault == "override":
        ref["overrides"]["quantity"] = .03
    elif fault == "remove":
        ref["removed_fields"] = ["missing"]
    elif fault == "hash":
        ref["restored_sha256"] = "unknown"
    else:
        receipt["raw_sha256"] = "wrong"
    with pytest.raises(ValueError):
        restore_model_input(projected)


def test_mapping_non_string_keys_and_heterogeneous_values_are_not_factored():
    for rows in ({1: {"x": 1}}, {"a": {"x": 1}, "b": {"y": 2}}):
        source = {"contract_context": {"ma_structure": {"levels": rows}}}
        projected, _ = project_model_input(source)
        assert projected == source


def test_effective_plan_delta_restores_receipt_identity():
    source = payload()
    receipt = source["calculated_candidates"]["candidates"][0]
    for row in [source["calculated_candidates"]["wait"], *source["calculated_candidates"]["candidates"]]:
        row["raw_model_wrapper"]["candidates"][0]["support_evidence"] = "unchanged " * 100
        row["raw_model_wrapper_sha256"] = digest(row["raw_model_wrapper"])
    receipt["raw_proposal"]["support_evidence"] = "unchanged " * 100
    effective = dict(receipt["raw_proposal"], hard_stop=85000.0,
                     take_profits=[{"price": 82000, "fraction": .5}],
                     numeric_types=[False, 1.0, 1])
    del effective["rationale"]
    receipt.update(effective_plan=effective, effective_sha256=digest(effective))
    receipt["receipt_sha256"] = digest(receipt)
    projected, _ = project_model_input(source)
    ref = projected["calculated_candidates"]["candidates"][0]["effective_plan"]
    assert ref["removed_fields"] == ["rationale"]
    assert ref["overrides"]["hard_stop"] == 85000.0
    restored = restore_model_input(json.loads(encode(projected)))
    assert encode(restored) == encode(source)
    for missing in ("overrides", "removed_fields", "restored_sha256"):
        bad = deepcopy(projected)
        del bad["calculated_candidates"]["candidates"][0]["effective_plan"][missing]
        with pytest.raises(ValueError):
            restore_model_input(bad)


def shared_payload():
    source = payload()
    for receipt in source["calculated_candidates"]["candidates"]:
        receipt["projection"] = {"cost_assumptions": "unchanged " * 250,
                                  "numeric_types": [True, 1, 1.0]}
    return source


def test_shared_receipt_values_preserve_exact_sorted_wire():
    source = shared_payload()
    projected, audit = project_model_input(source)
    assert projected["model_input_projection"]["receipt_value_paths"]
    assert digest(restore_model_input(json.loads(encode(projected)))) == digest(source)
    assert audit["projected_utf8_bytes"] < audit["original_utf8_bytes"]


@pytest.mark.parametrize("fault", ["tamper", "missing", "unknown", "orphan", "bad_path", "duplicate"])
def test_shared_receipt_values_fail_closed(fault):
    projected, _ = project_model_input(shared_payload())
    meta = projected["model_input_projection"]
    definitions = meta["receipt_value_definitions"]
    if fault == "tamper":
        definitions[next(iter(definitions))]["numeric_types"][1] = 1.0
    elif fault == "missing":
        meta.pop("receipt_value_paths")
    elif fault == "unknown":
        projected["calculated_candidates"]["candidates"][0]["projection"]["$receipt_value"] = "unknown"
    elif fault == "orphan":
        definitions["orphan"] = {}
    elif fault == "bad_path":
        meta["receipt_value_paths"][0] = "contract_context.positions"
    else:
        meta["receipt_value_paths"].append(meta["receipt_value_paths"][0])
    with pytest.raises(ValueError):
        restore_model_input(projected)


def test_valid_receipt_reference_swapping_is_detected_by_receipt_hash():
    source = shared_payload()
    for receipt in source["calculated_candidates"]["candidates"]:
        receipt["cost_semantics"] = {"cost_assumptions": "other " * 300}
        receipt["receipt_sha256"] = digest(receipt)
    projected, _ = project_model_input(source)
    rows = projected["calculated_candidates"]["candidates"]
    rows[0]["projection"] = deepcopy(rows[0]["cost_semantics"])
    with pytest.raises(ValueError, match="restored_receipt_hash_mismatch"):
        restore_model_input(projected)
