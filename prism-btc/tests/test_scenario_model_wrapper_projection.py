"""Lossless SELECT wrapper factoring, independent of economic authority."""
from copy import deepcopy
import hashlib
import json

import pytest

from live.scenario_model_input import project_model_input, restore_model_input


def encoded(value, sort_keys=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=sort_keys).encode()


def digest(value):
    return hashlib.sha256(encoded(value, True)).hexdigest()


def payload():
    wrapper = {"wait_proposal": {"action": "WAIT", "rationale": "관측" * 300},
               "candidates": [{"action": "ADJUST", "side": "SHORT", "hard_stop": 85000,
                               "rationale": "근거" * 300},
                              {"action": "EXIT", "rationale": "청산" * 300}],
               "no_candidate_reason": "", "types": [True, 1, 1.0]}
    receipts = []
    for key in ("WAIT", "c0", "c1"):
        receipt = {"id": key, "raw_model_wrapper": deepcopy(wrapper),
                   "raw_model_wrapper_sha256": digest(wrapper)}
        receipt["receipt_sha256"] = digest(receipt)
        receipts.append(receipt)
    return {"contract_context": {"positions": [{"side": "SHORT", "quantity": .02}],
                                 "fees_paid": 1.23, "previous_hard_stop": 85000},
            "market_snapshot": {}, "response_contract": {"selection_token": "unchanged"},
            "calculated_candidates": {"wait": receipts[0], "candidates": receipts[1:]}}


def test_wrapper_roundtrip_preserves_bytes_receipts_holdings_and_hashes():
    source = payload()
    before = deepcopy(source)
    projected, audit = project_model_input(source)
    assert source == before
    assert projected["model_input_projection"]["version"] == 3
    assert len(projected["model_input_projection"]["model_wrapper_definitions"]) == 1
    assert len(projected["model_input_projection"]["model_wrapper_paths"]) == 3
    assert audit["original_utf8_bytes"] - audit["projected_utf8_bytes"] > 5000
    restored = restore_model_input(projected)
    assert encoded(restored) == encoded(source)
    assert digest(restored) == audit["original_semantic_sha256"]
    assert hashlib.sha256(encoded(restored)).hexdigest() == audit["original_sha256"]
    assert projected["contract_context"] == source["contract_context"]
    assert projected["response_contract"] == source["response_contract"]
    # The actual request writer canonicalizes key order before transmission.
    restored_wire = restore_model_input(json.loads(encoded(projected, True)))
    assert encoded(restored_wire, True) == encoded(source, True)
    assert digest(restored_wire) == audit["original_semantic_sha256"]


@pytest.mark.parametrize("fault", ["unknown", "tamper", "wrong_hash", "unregistered", "duplicate_path", "bad_path", "orphan_definition"])
def test_wrapper_reference_tampering_fails_closed(fault):
    projected, _ = project_model_input(payload())
    metadata = projected["model_input_projection"]
    key = next(iter(metadata["model_wrapper_definitions"]))
    receipt = projected["calculated_candidates"]["wait"]
    if fault == "unknown":
        receipt["raw_model_wrapper"]["$model_wrapper"] = "unknown"
    elif fault == "tamper":
        metadata["model_wrapper_definitions"][key]["types"][1] = 1.0
    elif fault == "wrong_hash":
        receipt["raw_model_wrapper_sha256"] = "bad"
    elif fault == "unregistered":
        metadata["model_wrapper_paths"].pop()
    elif fault == "duplicate_path":
        metadata["model_wrapper_paths"].append(metadata["model_wrapper_paths"][0])
    elif fault == "bad_path":
        metadata["model_wrapper_paths"][0] = "contract_context.positions"
    else:
        metadata["model_wrapper_definitions"]["orphan"] = {}
    with pytest.raises(ValueError):
        restore_model_input(projected)


def test_unregistered_wrapper_marker_rejected_without_projection_metadata():
    with pytest.raises(ValueError, match="unregistered_model_wrapper"):
        restore_model_input({"x": {"$model_wrapper": "unknown"}})
    with pytest.raises(ValueError, match="reserved_projection_field"):
        project_model_input({"x": {"$model_wrapper": "unknown"}})


def test_three_candidates_are_not_implicitly_accepted_or_factored():
    source = payload()
    source["calculated_candidates"]["candidates"].append(
        deepcopy(source["calculated_candidates"]["candidates"][0]))
    projected, audit = project_model_input(source)
    assert encoded(projected) == encoded(source)
    assert audit["factored_paths"] == []


def test_wrapper_and_row_tables_restore_together():
    source = payload()
    source["market_snapshot"] = {"timeframes": {"30m": {"recent_confirmed_bars": [
        {"long_column_one": 1, "long_column_two": 1.0, "long_column_three": True}
        for _ in range(20)]}}}
    projected, _ = project_model_input(source)
    assert projected["model_input_projection"]["version"] == 3
    assert projected["model_input_projection"]["row_table_paths"]
    assert encoded(restore_model_input(projected)) == encoded(source)


def test_distinct_wrapper_values_never_merge_and_small_wrappers_never_inflate():
    source = payload()
    receipts = [source["calculated_candidates"]["wait"], *source["calculated_candidates"]["candidates"]]
    for i, receipt in enumerate(receipts):
        receipt["raw_model_wrapper"]["different"] = i
        receipt["raw_model_wrapper_sha256"] = digest(receipt["raw_model_wrapper"])
    projected, _ = project_model_input(source)
    assert encoded(projected) == encoded(source)
    for receipt in receipts:
        receipt["raw_model_wrapper"] = {"small": True}
        receipt["raw_model_wrapper_sha256"] = digest(receipt["raw_model_wrapper"])
    projected, audit = project_model_input(source)
    assert encoded(projected) == encoded(source)
    assert audit["original_utf8_bytes"] == audit["projected_utf8_bytes"]
