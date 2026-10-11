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


def _has_table_marker(value):
    if isinstance(value, dict):
        return "$row_table" in value or any(_has_table_marker(v) for v in value.values())
    return isinstance(value, list) and any(_has_table_marker(v) for v in value)


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
        if not isinstance(body, dict) or set(body) != {"columns", "rows"}:
            raise ValueError("invalid_row_table")
        columns, rows = body["columns"], body["rows"]
        if (not isinstance(columns, list) or not columns or
                any(not isinstance(c, str) for c in columns) or len(columns) != len(set(columns)) or
                not isinstance(rows, list) or not rows or
                any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
            raise ValueError("invalid_row_table_shape")
        owner[key] = [dict(zip(columns, row)) for row in rows]
    if _has_table_marker(original):
        raise ValueError("unregistered_row_table")


def project_model_input(payload):
    """Return independent projected payload and value-free byte/hash/path audit."""
    original = _json(payload)
    projected = deepcopy(payload)
    if "model_input_projection" in payload or _has_table_marker(payload):
        raise ValueError("reserved_projection_field")
    references, definitions, paths = [], {}, []
    recovery = projected.get("contract_context", {}).get("recovery")
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
    table_paths = []
    for path, owner, key in _table_locations(projected):
        rows = owner.get(key)
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict) or not rows[0]:
            continue
        columns = list(rows[0])
        if any(not isinstance(c, str) for c in columns) or any(not isinstance(row, dict) or list(row) != columns for row in rows):
            continue
        table = {"$row_table": {"columns": columns, "rows": [[row[c] for c in columns] for row in rows]}}
        if len(_json(table).encode()) < len(_json(rows).encode()):
            owner[key] = table
            table_paths.append(path)
            paths.append(path)
    if paths:
        projected["model_input_projection"] = {
            "version": 2 if table_paths else 1, "numeric_market_references": references, "feature_definitions": definitions,
            "meaning": "Lossless references only. Resolve numeric_market-v1 as the supplied snapshot numeric paths; feature-definition references use this shared dictionary. All original values are retained or exactly reproducible."}
        if table_paths:
            projected["model_input_projection"]["row_table_paths"] = table_paths
            projected["model_input_projection"]["meaning"] += " At each row_table_path, $row_table.columns names each cell in every rows array, in order; each array is one original observation object. Expand tables before resolving numeric_market-v1."
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
        if _has_table_marker(original):
            raise ValueError("unregistered_row_table")
        return original
    if not isinstance(metadata, dict) or type(metadata.get("version")) is not int or metadata["version"] not in (1, 2):
        raise ValueError("unknown_projection_version")
    if metadata["version"] == 2:
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
    return original
