"""Reversible model-only factoring of exact duplicates; never truncate evidence."""
from copy import deepcopy
import hashlib
import json


def _json(value, *, sort_keys=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=sort_keys)


def _definition_rows(payload, name):
    snapshot = payload.get("market_snapshot")
    frames = snapshot.get("timeframes", {}) if isinstance(snapshot, dict) else None
    if not isinstance(frames, dict):
        return []
    return [(f"market_snapshot.timeframes.{tf}.{kind}.{name}", feature)
            for tf, frame in frames.items() if isinstance(frame, dict)
            for kind in ("confirmed", "forming")
            if isinstance(feature := frame.get(kind), dict) and name in feature]


def _table_locations(payload):
    """Only market observation row lists, never account or response-contract shapes."""
    snapshot = payload.get("market_snapshot")
    frames = snapshot.get("timeframes", {}) if isinstance(snapshot, dict) else {}
    if isinstance(frames, dict):
        for tf, frame in frames.items():
            if not isinstance(frame, dict):
                continue
            yield f"market_snapshot.timeframes.{tf}.recent_confirmed_bars", frame, "recent_confirmed_bars"
            if isinstance(frame.get("ma_path"), dict):
                yield f"market_snapshot.timeframes.{tf}.ma_path.confirmed_points", frame["ma_path"], "confirmed_points"
    context = payload.get("contract_context")
    structure = context.get("ma_structure") if isinstance(context, dict) else None
    primary = structure.get("primary", {}) if isinstance(structure, dict) else {}
    if isinstance(primary, dict):
        for tf, frame in primary.items():
            if isinstance(frame, dict):
                for key in ("points", "transitions"):
                    yield f"contract_context.ma_structure.primary.{tf}.{key}", frame, key
    if isinstance(structure, dict):
        yield "contract_context.ma_structure.levels", structure, "levels"
    recovery = context.get("recovery") if isinstance(context, dict) else None
    if isinstance(recovery, dict):
        yield "contract_context.recovery.changed_evidence", recovery, "changed_evidence"


def _has_table_marker(value):
    if isinstance(value, dict):
        return "$row_table" in value or any(_has_table_marker(v) for v in value.values())
    return isinstance(value, list) and any(_has_table_marker(v) for v in value)


def _has_wrapper_marker(value):
    if isinstance(value, dict):
        return "$model_wrapper" in value or any(_has_wrapper_marker(v) for v in value.values())
    return isinstance(value, list) and any(_has_wrapper_marker(v) for v in value)


def _has_proposal_marker(value):
    if isinstance(value, dict):
        return "$model_proposal" in value or any(_has_proposal_marker(v) for v in value.values())
    return isinstance(value, list) and any(_has_proposal_marker(v) for v in value)


def _has_receipt_marker(value):
    if isinstance(value, dict):
        return "$receipt_value" in value or any(_has_receipt_marker(v) for v in value.values())
    return isinstance(value, list) and any(_has_receipt_marker(v) for v in value)


def _has_recovery_marker(value):
    if isinstance(value, dict):
        return "$recovery_changes" in value or any(_has_recovery_marker(v) for v in value.values())
    return isinstance(value, list) and any(_has_recovery_marker(v) for v in value)


def _recovery_changes(recovery):
    if not isinstance(recovery, dict):
        return None
    baseline, current = recovery.get("baseline"), recovery.get("current")
    if not all(isinstance(v, dict) and all(isinstance(k, str) and type(n) in (int, float)
               for k, n in v.items()) for v in (baseline, current)):
        return None
    # Match prepare_input exactly: numeric equality determines changed paths;
    # canonical JSON equality below separately preserves int/float distinctions.
    return {path: {"before": baseline[path], "now": value}
            for path, value in current.items() if path in baseline and baseline[path] != value}


def _restore_recovery_changes(original, metadata):
    if metadata.get("recovery_changed_evidence_path") != "contract_context.recovery.changed_evidence":
        raise ValueError("invalid_recovery_change_path")
    recovery = (original.get("contract_context") or {}).get("recovery")
    reference = recovery.get("changed_evidence") if isinstance(recovery, dict) else None
    if (not isinstance(reference, dict) or set(reference) not in (
            {"$recovery_changes", "restored_sha256", "key_order"},
            {"$recovery_changes", "restored_sha256", "key_order", "path_prefix"})
            or reference.get("$recovery_changes") != "baseline-current-v1"):
        raise ValueError("invalid_recovery_change_reference")
    if "path_prefix" in reference and reference["path_prefix"] != "timeframes.":
        raise ValueError("invalid_recovery_change_prefix")
    changes = _recovery_changes(recovery)
    if changes is None:
        raise ValueError("invalid_recovery_change_sources")
    if "key_order" in reference:
        order = reference["key_order"]
        if not isinstance(order, list) or any(not isinstance(k, str) for k in order):
            raise ValueError("invalid_recovery_change_order")
        order = [reference.get("path_prefix", "") + key for key in order]
        if len(order) != len(set(order)) or set(order) != set(changes):
            raise ValueError("invalid_recovery_change_order")
        changes = {key: changes[key] for key in order}
    if hashlib.sha256(_json(changes).encode()).hexdigest() != reference["restored_sha256"]:
        raise ValueError("recovery_change_hash_mismatch")
    recovery["changed_evidence"] = changes
    if _has_recovery_marker(original):
        raise ValueError("unregistered_recovery_change")


def _receipt_locations(payload):
    for path, receipt in _wrapper_locations(payload):
        if isinstance(receipt, dict):
            for field in ("allocation_receipt", "cost_semantics", "effective_per_entry_risk",
                          "raw_per_entry_risk", "raw_risk", "pricing_basis", "projection",
                          "effective_plan", "raw_proposal"):
                if field in receipt:
                    yield path.rsplit(".", 1)[0] + "." + field, receipt, field


def _restore_receipt_values(original, metadata):
    definitions, paths = metadata.get("receipt_value_definitions"), metadata.get("receipt_value_paths")
    if (not isinstance(definitions, dict) or not definitions or not isinstance(paths, list)
            or not paths or any(not isinstance(p, str) for p in paths) or len(paths) != len(set(paths))):
        raise ValueError("invalid_receipt_value_projection")
    locations = {p: (r, f) for p, r, f in _receipt_locations(original)}
    used = set()
    for path in paths:
        if path not in locations:
            raise ValueError("unknown_receipt_value_path")
        receipt, field = locations[path]
        ref = receipt[field]
        if not isinstance(ref, dict) or set(ref) != {"$receipt_value"}:
            raise ValueError("invalid_receipt_value_reference")
        key = ref["$receipt_value"]
        if not isinstance(key, str) or key not in definitions:
            raise ValueError("unknown_receipt_value_reference")
        value = definitions[key]
        if (_has_receipt_marker(value) or hashlib.sha256(_json(value, sort_keys=True).encode()).hexdigest() != key):
            raise ValueError("receipt_value_hash_mismatch")
        receipt[field] = deepcopy(value)
        used.add(key)
    if used != set(definitions) or _has_receipt_marker(original):
        raise ValueError("unregistered_receipt_value")


def _proposal_locations(payload):
    for path, receipt in _wrapper_locations(payload):
        if isinstance(receipt, dict):
            for field in ("raw_allocation_proposal", "raw_proposal", "effective_plan"):
                yield path.rsplit(".", 1)[0] + "." + field, receipt, field


def _wrapper_proposal(receipt):
    wrapper = receipt.get("raw_model_wrapper")
    if not isinstance(wrapper, dict):
        return None
    if receipt.get("id") == "WAIT":
        return wrapper.get("wait_proposal")
    candidates = wrapper.get("candidates")
    if isinstance(candidates, list):
        for i, proposal in enumerate(candidates):
            if receipt.get("id") == f"c{i}":
                return proposal
    return None


def _restore_proposals(original, metadata):
    paths = metadata.get("model_proposal_paths")
    if (not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths)
            or len(paths) != len(set(paths))):
        raise ValueError("invalid_model_proposal_paths")
    locations = {p: (r, f) for p, r, f in _proposal_locations(original)}
    for path in paths:
        if path not in locations:
            raise ValueError("unknown_model_proposal_path")
        receipt, field = locations[path]
        ref, proposal = receipt.get(field), _wrapper_proposal(receipt)
        if (not isinstance(ref, dict) or set(ref) not in ({"$model_proposal"},
                {"$model_proposal", "overrides", "removed_fields", "restored_sha256"})
                or not isinstance(proposal, dict) or _has_proposal_marker(proposal)
                or hashlib.sha256(_json(receipt.get("raw_model_wrapper"), sort_keys=True).encode()).hexdigest()
                != receipt.get("raw_model_wrapper_sha256")
                or ref["$model_proposal"] != hashlib.sha256(_json(proposal, sort_keys=True).encode()).hexdigest()):
            raise ValueError("model_proposal_hash_mismatch")
        restored = deepcopy(proposal)
        if "overrides" in ref:
            overrides, removed = ref["overrides"], ref["removed_fields"]
            if (not isinstance(overrides, dict) or _has_proposal_marker(overrides)
                    or not isinstance(removed, list) or any(not isinstance(k, str) for k in removed)
                    or len(removed) != len(set(removed)) or any(k not in proposal for k in removed)
                    or set(removed) & set(overrides)):
                raise ValueError("invalid_model_proposal_delta")
            for key in removed:
                del restored[key]
            restored.update(overrides)
            if hashlib.sha256(_json(restored, sort_keys=True).encode()).hexdigest() != ref["restored_sha256"]:
                raise ValueError("model_proposal_delta_hash_mismatch")
        hash_field = {"raw_allocation_proposal": "raw_allocation_sha256", "raw_proposal": "raw_sha256",
                      "effective_plan": "effective_sha256"}[field]
        if hash_field in receipt and receipt[hash_field] != hashlib.sha256(_json(restored, sort_keys=True).encode()).hexdigest():
            raise ValueError("model_proposal_receipt_hash_mismatch")
        receipt[field] = restored
    if _has_proposal_marker(original):
        raise ValueError("unregistered_model_proposal")


def _wrapper_locations(payload):
    bundle = payload.get("calculated_candidates")
    if not isinstance(bundle, dict):
        return []
    candidates = bundle.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 2:
        return []
    return [("calculated_candidates.wait.raw_model_wrapper", bundle.get("wait"))] + [
        (f"calculated_candidates.candidates.{i}.raw_model_wrapper", receipt)
        for i, receipt in enumerate(candidates)]


def _restore_wrappers(original, metadata):
    definitions, paths = metadata.get("model_wrapper_definitions"), metadata.get("model_wrapper_paths")
    if (not isinstance(definitions, dict) or not definitions or not isinstance(paths, list)
            or not paths or any(not isinstance(p, str) for p in paths) or len(paths) != len(set(paths))):
        raise ValueError("invalid_model_wrapper_projection")
    locations = dict(_wrapper_locations(original))
    used = set()
    for path in paths:
        receipt = locations.get(path)
        ref = receipt.get("raw_model_wrapper") if isinstance(receipt, dict) else None
        if not isinstance(ref, dict) or set(ref) != {"$model_wrapper"}:
            raise ValueError("invalid_model_wrapper_reference")
        key = ref["$model_wrapper"]
        if not isinstance(key, str) or key not in definitions:
            raise ValueError("unknown_model_wrapper_reference")
        wrapper = definitions[key]
        if (not isinstance(wrapper, dict) or _has_wrapper_marker(wrapper)
                or hashlib.sha256(_json(wrapper, sort_keys=True).encode()).hexdigest() != key
                or hashlib.sha256(_json(wrapper, sort_keys=True).encode()).hexdigest()
                != receipt.get("raw_model_wrapper_sha256")):
            raise ValueError("model_wrapper_hash_mismatch")
        receipt["raw_model_wrapper"] = deepcopy(wrapper)
        used.add(key)
    if used != set(definitions) or _has_wrapper_marker(original):
        raise ValueError("unregistered_model_wrapper")


def _restore_tables(original, metadata):
    paths = metadata.get("row_table_paths")
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths) or len(paths) != len(set(paths)):
        raise ValueError("invalid_row_table_paths")
    locations = {path: (owner, key) for path, owner, key in _table_locations(original)}
    for path in paths:
        if path not in locations:
            raise ValueError("invalid_row_table_path")
        owner, key = locations[path]
        table = owner.get(key)
        if not isinstance(table, dict) or set(table) != {"$row_table"}:
            raise ValueError("invalid_row_table")
        body = table["$row_table"]
        if not isinstance(body, dict) or set(body) not in ({"columns", "rows"}, {"columns", "rows", "keys"}):
            raise ValueError("invalid_row_table")
        columns, rows = body["columns"], body["rows"]
        if path in {"contract_context.ma_structure.levels", "contract_context.recovery.changed_evidence"} and "keys" not in body:
            raise ValueError("missing_row_table_keys")
        if (not isinstance(columns, list) or not columns or
                any(not isinstance(c, str) for c in columns) or len(columns) != len(set(columns)) or
                not isinstance(rows, list) or not rows or
                any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
            raise ValueError("invalid_row_table_shape")
        values = [dict(zip(columns, row)) for row in rows]
        if "keys" in body:
            keys = body["keys"]
            if (metadata["version"] not in (4, 5) or path not in {
                    "contract_context.ma_structure.levels", "contract_context.recovery.changed_evidence"}
                    or not isinstance(keys, list) or len(keys) != len(rows)
                    or any(not isinstance(k, str) for k in keys) or len(keys) != len(set(keys))):
                raise ValueError("invalid_row_table_keys")
            owner[key] = dict(zip(keys, values))
        else:
            owner[key] = values
    if _has_table_marker(original):
        raise ValueError("unregistered_row_table")


def project_model_input(payload):
    """Return independent projected payload and value-free byte/hash/path audit."""
    original = _json(payload)
    projected = deepcopy(payload)
    if ("model_input_projection" in payload or _has_table_marker(payload)
            or _has_wrapper_marker(payload) or _has_proposal_marker(payload) or _has_receipt_marker(payload)
            or _has_recovery_marker(payload)):
        raise ValueError("reserved_projection_field")
    references, definitions, paths = [], {}, []
    recovery = projected.get("contract_context", {}).get("recovery")
    recovery_change_path = None
    changes = _recovery_changes(recovery)
    supplied_changes = recovery.get("changed_evidence") if isinstance(recovery, dict) else None
    if (changes and isinstance(supplied_changes, dict)
            and all(isinstance(v, dict) and list(v) == ["before", "now"] for v in supplied_changes.values())
            and _json(changes, sort_keys=True) == _json(supplied_changes, sort_keys=True)):
        reference = {"$recovery_changes": "baseline-current-v1",
                     "restored_sha256": hashlib.sha256(_json(supplied_changes).encode()).hexdigest(),
                     "key_order": list(supplied_changes)}
        if all(key.startswith("timeframes.") for key in supplied_changes):
            prefixed = dict(reference, path_prefix="timeframes.",
                            key_order=[key[len("timeframes."):] for key in supplied_changes])
            if len(_json(prefixed).encode()) < len(_json(reference).encode()):
                reference = prefixed
        if len(_json(reference).encode()) + 600 < len(_json(supplied_changes).encode()):
            recovery["changed_evidence"] = reference
            recovery_change_path = "contract_context.recovery.changed_evidence"
            paths.append(recovery_change_path)
    if isinstance(recovery, dict) and isinstance(recovery.get("current"), dict):
        from live.scenario_recovery import numeric_market
        try:
            current = numeric_market(projected["market_snapshot"])
        except (AttributeError, KeyError, TypeError, ValueError):
            current = None
        # JSON comparison is type-sensitive (True != 1, 1 != 1.0), unlike dict equality.
        if current is not None and _json(current, sort_keys=True) == _json(recovery["current"], sort_keys=True):
            recovery["current"] = {"$ref": "market_snapshot", "projection": "numeric_market-v1"}
            references.append("contract_context.recovery.current")
            paths.append("contract_context.recovery.current")
    for name in ("compression_definition", "convergence_definition"):
        rows = _definition_rows(projected, name)
        values = [row[name] for _, row in rows]
        if len(values) > 1 and all(isinstance(v, str) and v == values[0] for v in values):
            definitions[name] = values[0]
            for path, row in rows:
                row[name] = {"$feature_definition": name}
                paths.append(path)
    table_paths, keyed_tables = [], False
    for path, owner, key in _table_locations(projected):
        rows = owner.get(key)
        keys = None
        if path in {"contract_context.ma_structure.levels", "contract_context.recovery.changed_evidence"}:
            if not isinstance(rows, dict):
                continue
            if any(not isinstance(k, str) for k in rows):
                continue
            keys, rows = list(rows), list(rows.values())
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict) or not rows[0]:
            continue
        columns = list(rows[0])
        if any(not isinstance(c, str) for c in columns) or any(not isinstance(row, dict) or list(row) != columns for row in rows):
            continue
        table = {"$row_table": {"columns": columns, "rows": [[row[c] for c in columns] for row in rows]}}
        if keys is not None:
            table["$row_table"]["keys"] = keys
        if len(_json(table).encode()) < len(_json(owner[key]).encode()):
            owner[key] = table
            table_paths.append(path)
            paths.append(path)
            keyed_tables |= keys is not None
    proposal_paths = []
    for path, receipt, field in _proposal_locations(projected):
        proposal, value = _wrapper_proposal(receipt), receipt.get(field)
        wrapper = receipt.get("raw_model_wrapper")
        if (isinstance(proposal, dict) and isinstance(value, dict)
                and hashlib.sha256(_json(wrapper, sort_keys=True).encode()).hexdigest()
                == receipt.get("raw_model_wrapper_sha256")):
            ref = {"$model_proposal": hashlib.sha256(_json(proposal, sort_keys=True).encode()).hexdigest()}
            if _json(proposal, sort_keys=True) != _json(value, sort_keys=True):
                ref.update(overrides={k: deepcopy(v) for k, v in value.items()
                           if k not in proposal or _json(v, sort_keys=True) != _json(proposal[k], sort_keys=True)},
                           removed_fields=[k for k in proposal if k not in value],
                           restored_sha256=hashlib.sha256(_json(value, sort_keys=True).encode()).hexdigest())
            hash_field = {"raw_allocation_proposal": "raw_allocation_sha256", "raw_proposal": "raw_sha256",
                          "effective_plan": "effective_sha256"}[field]
            if hash_field in receipt and receipt[hash_field] != hashlib.sha256(_json(value, sort_keys=True).encode()).hexdigest():
                continue
            if len(_json(ref)) < len(_json(value)):
                receipt[field] = ref
                proposal_paths.append(path)
                paths.append(path)
    wrapper_definitions, wrapper_paths = {}, []
    groups = {}
    for path, receipt in _wrapper_locations(projected):
        wrapper = receipt.get("raw_model_wrapper") if isinstance(receipt, dict) else None
        if (isinstance(wrapper, dict) and hashlib.sha256(_json(wrapper, sort_keys=True).encode()).hexdigest()
                == receipt.get("raw_model_wrapper_sha256")):
            groups.setdefault(_json(wrapper), []).append((path, receipt))
    for locations in groups.values():
        if len(locations) < 2:
            continue
        key = hashlib.sha256(_json(locations[0][1]["raw_model_wrapper"], sort_keys=True).encode()).hexdigest()
        wrapper_definitions[key] = deepcopy(locations[0][1]["raw_model_wrapper"])
        for path, receipt in locations:
            receipt["raw_model_wrapper"] = {"$model_wrapper": key}
            wrapper_paths.append(path)
            paths.append(path)
    value_definitions, value_paths, groups = {}, [], {}
    for path, receipt, field in _receipt_locations(projected):
        value = receipt[field]
        if isinstance(value, (dict, list)) and len(_json(value)) >= 400:
            groups.setdefault(_json(value, sort_keys=True), []).append((path, receipt, field))
    for encoded_value, locations in groups.items():
        if len(locations) < 2:
            continue
        key = hashlib.sha256(encoded_value.encode()).hexdigest()
        ref = {"$receipt_value": key}
        overhead = len(_json(ref)) * len(locations) + len(_json([p for p, _, _ in locations])) + 70
        if len(encoded_value) * (len(locations) - 1) <= overhead:
            continue
        value_definitions[key] = deepcopy(locations[0][1][locations[0][2]])
        for path, receipt, field in locations:
            receipt[field] = deepcopy(ref)
            value_paths.append(path)
            paths.append(path)
    if paths:
        projected["model_input_projection"] = {
            "version": 5 if recovery_change_path else 4 if proposal_paths or keyed_tables or value_paths else 3 if wrapper_paths else 2 if table_paths else 1, "numeric_market_references": references, "feature_definitions": definitions,
            "meaning": "Lossless references only. Resolve numeric_market-v1 as the supplied snapshot numeric paths; feature-definition references use this shared dictionary. All original values are retained or exactly reproducible."}
        if table_paths:
            projected["model_input_projection"]["row_table_paths"] = table_paths
            projected["model_input_projection"]["meaning"] += " At each row_table_path, $row_table.columns names each cell in every rows array, in order; each array is one original observation object. Expand tables before resolving numeric_market-v1."
            if keyed_tables:
                projected["model_input_projection"]["meaning"] += " When keys is present, keys[i] names rows[i] in the original object mapping."
        if wrapper_paths:
            projected["model_input_projection"].update(model_wrapper_definitions=wrapper_definitions,
                                                      model_wrapper_paths=wrapper_paths)
            projected["model_input_projection"]["meaning"] += " Each $model_wrapper refers to the exact shared raw model wrapper in model_wrapper_definitions. Restore it before interpreting original receipt or bundle hashes; raw_model_wrapper_sha256 and receipt identities are unchanged."
        if proposal_paths:
            projected["model_input_projection"]["model_proposal_paths"] = proposal_paths
            projected["model_input_projection"]["meaning"] += " Each $model_proposal is the SHA256 of the base proposal in that receipt's raw_model_wrapper: WAIT uses wait_proposal; cN uses candidates[N]. If present, remove removed_fields then replace/add top-level overrides to obtain the exact restored_sha256 proposal (including host-sized quantity). Restore wrappers first, then these proposals before interpreting receipt hashes. No strategic values are changed."
        if value_paths:
            projected["model_input_projection"].update(receipt_value_definitions=value_definitions,
                                                     receipt_value_paths=value_paths)
            projected["model_input_projection"]["meaning"] += " First expand $receipt_value using receipt_value_definitions (key is its SHA256); these are exact shared receipt blocks, not shared decisions. Then restore wrappers and proposals. Effective-plan overrides apply only to that effective_plan, never to the raw proposal."
        if recovery_change_path:
            projected["model_input_projection"]["recovery_changed_evidence_path"] = recovery_change_path
            projected["model_input_projection"]["meaning"] += " After expanding tables and numeric_market-v1 current, $recovery_changes baseline-current-v1 provides host-computed ordered changed paths in key_order; prepend path_prefix to each key when present. Do NOT calculate which paths changed. For each full path, read changed_evidence[path]={before:baseline[path],now:current[path]}. Preserve source numeric types. restored_sha256 verifies insertion-order compact UTF-8 JSON of reconstructed changes. No changed facts are omitted."
    encoded = _json(projected)
    if len(encoded.encode()) >= len(original.encode()):
        projected, encoded, paths = deepcopy(payload), original, []
    audit = {"version": projected.get("model_input_projection", {}).get("version", 1), "original_utf8_bytes": len(original.encode()),
             "projected_utf8_bytes": len(encoded.encode()),
             "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
             "projected_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
             "wire_hash_basis": "compact_utf8_json_insertion_order",
             "semantic_hash_basis": "compact_utf8_json_sorted_keys_preserves_numeric_types",
             "original_semantic_sha256": hashlib.sha256(_json(payload, sort_keys=True).encode()).hexdigest(),
             "projected_semantic_sha256": hashlib.sha256(_json(projected, sort_keys=True).encode()).hexdigest(),
             "factored_paths": paths}
    return projected, audit


def restore_model_input(payload):
    """Deterministic reconstruction for audit/tests; no network or model calls."""
    original = deepcopy(payload)
    metadata = original.pop("model_input_projection", None)
    if metadata is None:
        if _has_recovery_marker(original):
            raise ValueError("unregistered_recovery_change")
        if _has_receipt_marker(original):
            raise ValueError("unregistered_receipt_value")
        if _has_proposal_marker(original):
            raise ValueError("unregistered_model_proposal")
        if _has_wrapper_marker(original):
            raise ValueError("unregistered_model_wrapper")
        if _has_table_marker(original):
            raise ValueError("unregistered_row_table")
        return original
    if not isinstance(metadata, dict) or type(metadata.get("version")) is not int or metadata["version"] not in (1, 2, 3, 4, 5):
        raise ValueError("unknown_projection_version")
    if metadata["version"] in (4, 5) and "receipt_value_paths" in metadata:
        _restore_receipt_values(original, metadata)
    elif _has_receipt_marker(original):
        raise ValueError("unregistered_receipt_value")
    if metadata["version"] == 3 or (metadata["version"] in (4, 5) and "model_wrapper_paths" in metadata):
        _restore_wrappers(original, metadata)
    elif _has_wrapper_marker(original):
        raise ValueError("unregistered_model_wrapper")
    if metadata["version"] in (4, 5) and "model_proposal_paths" in metadata:
        _restore_proposals(original, metadata)
    elif _has_proposal_marker(original):
        raise ValueError("unregistered_model_proposal")
    if metadata["version"] == 2 or (metadata["version"] in (3, 4, 5) and "row_table_paths" in metadata):
        _restore_tables(original, metadata)
    elif _has_table_marker(original):
        raise ValueError("unregistered_row_table")
    for name, definition in metadata["feature_definitions"].items():
        for _, row in _definition_rows(original, name):
            if row[name] == {"$feature_definition": name}:
                row[name] = definition
    if "contract_context.recovery.current" in metadata["numeric_market_references"]:
        from live.scenario_recovery import numeric_market
        original["contract_context"]["recovery"]["current"] = numeric_market(original["market_snapshot"])
    if metadata["version"] == 5:
        _restore_recovery_changes(original, metadata)
    elif _has_recovery_marker(original) or "recovery_changed_evidence_path" in metadata:
        raise ValueError("unregistered_recovery_change")
    if metadata["version"] in (4, 5):
        for _, receipt in _wrapper_locations(original):
            if isinstance(receipt, dict) and "receipt_sha256" in receipt:
                body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
                if hashlib.sha256(_json(body, sort_keys=True).encode()).hexdigest() != receipt["receipt_sha256"]:
                    raise ValueError("restored_receipt_hash_mismatch")
    return original
