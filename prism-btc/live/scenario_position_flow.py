"""Read-only, bounded scenario history. Historical opinions are not market facts."""
import copy
import json
import math
import sqlite3

from live.scenario_entry_contract import valid_record
from live.scenario_model_input import project_model_input

MAX_BYTES = 6000
SCAN_LIMIT = 288
_ROW_BYTES = 32768
_MODEL_INPUT_LIMIT = 100_000
_CONTEXT_FIELDS = ("scenario_id", "now", "input_captured_at", "account_captured_at",
                   "mark_price", "positions", "previous_hard_stop", "target_status",
                   "accounting_status", "recorded_scenario_net")


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _pick(value, keys):
    return {k: copy.deepcopy(value[k]) for k in keys if k in value}


def _object(raw):
    if not isinstance(raw, str) or len(raw.encode()) > _ROW_BYTES:
        raise ValueError("history_row_size")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("history_row_not_object")
    _size(value)
    return value


def _plan(value):
    return _pick(value, ("action_id", "action", "side", "revision", "entries", "hard_stop",
                         "take_profits", "partial_stops", "rationale", "expires_at"))


def _observation(context, now):
    stamp = context.get("account_captured_at")
    if not _number(stamp) or not 0 < stamp <= now:
        return dict(status="UNAVAILABLE", reason="missing_or_future_capture_time")
    return dict(source="SAVED_ACCOUNT_OBSERVATION_NOT_FILL_EVENT", captured_at=stamp,
                **_pick(context, ("positions", "previous_hard_stop", "target_status",
                                  "recorded_scenario_net", "accounting_status", "mark_price")))


def project_position_flow_input(payload):
    """Fit optional history only; preserve the mandatory input size rejection.

    Projection audit hashes describe the actual trimmed input. The frozen source
    is untouched, and every omission is disclosed inside the model-facing flow.
    """
    projected, audit = project_model_input(payload)
    flow = (payload.get("contract_context") or {}).get("position_flow")
    if _size(projected) <= _MODEL_INPUT_LIMIT or not isinstance(flow, dict):
        return projected, audit
    candidate = copy.deepcopy(payload)
    flow = candidate["contract_context"]["position_flow"]
    fields = ("historical_observation_changes", "requested_changes", "thesis_transitions")
    coverage = flow.get("coverage")
    if not isinstance(coverage, dict):
        coverage = flow["coverage"] = {}
    omitted = 0
    # The builder retains at most 3 + 3 + 4 records. Even malformed optional
    # input cannot turn this budget pass into an unbounded projection loop.
    for _ in range(10):
        field = next((key for key in fields if isinstance(flow.get(key), list) and flow[key]), None)
        if field is None:
            break
        del flow[field][0]
        omitted += 1
        coverage["model_budget_omitted_items"] = omitted
        flow["status"] = "PARTIAL"
        projected, audit = project_model_input(candidate)
        if _size(projected) <= _MODEL_INPUT_LIMIT:
            return projected, audit
    candidate["contract_context"]["position_flow"] = dict(version=1,
        status="OMITTED_INPUT_BUDGET",
        scope="Refer to registered_entry_contract/entry_thesis/previous_thesis_assessment; history unavailable.")
    # If mandatory market/account/candidate evidence still exceeds the cap, the
    # existing caller rejects it. Never trim safety or candidate evidence here.
    return project_model_input(candidate)


def build_position_flow(conn, active, context, now):
    try:
        return _build_position_flow(conn, active, context, now)
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        # Optional continuity must never prevent independent protection checks.
        return dict(version=1, status="UNAVAILABLE", reason="malformed_optional_history")


def _build_position_flow(conn, active, context, now):
    """Project saved same-scenario events without SQL writes or external dependencies.

    Context is the existing broker-verified context, not an independent observation.
    Latest global decision slots are scanned with a bound, so older coverage is
    explicitly unknown. No order intent or cumulative action fill becomes a fill.
    """
    if not isinstance(active, dict) or not active.get("scenario_id"):
        return dict(version=1, status="NOT_ACTIVE")
    sid = active["scenario_id"]
    if not isinstance(sid, str) or len(sid) > 128 or not _number(now) or now <= 0:
        return dict(version=1, status="UNAVAILABLE", reason="invalid_identity_or_time")
    if context.get("scenario_id") != sid:
        return dict(version=1, status="UNAVAILABLE", reason="context_scenario_mismatch")
    created = active.get("created_at")
    if created is not None and (not _number(created) or created > now):
        return dict(version=1, status="UNAVAILABLE", reason="invalid_scenario_time")
    coverage = dict(scan_limit=SCAN_LIMIT, scanned_rows=0, same_scenario_rows=0,
                    rejected_rows=0, invalid_rows=0, omitted_items=0,
                    older_history="UNKNOWN", source="LOCAL_DURABLE_LEDGER")
    result = dict(version=1, scenario_id=sid, as_of=now, status="AVAILABLE",
                  scope="Historical opinions and requested plans are not current facts or fills; missing history is unknown.",
                  coverage=coverage)

    def add(key, value, limit=2400):
        try:
            if _size(value) > limit:
                raise ValueError("item_size")
            result[key] = copy.deepcopy(value)
            if _size(result) > MAX_BYTES - 512:
                del result[key]
                raise ValueError("total_size")
        except (ValueError, TypeError, OverflowError):
            coverage["omitted_items"] += 1
            result["status"] = "PARTIAL"

    def recent(key, values, count):
        # Drop whole oldest records, never cut a reason or numeric literal.
        retained = values[-count:]
        coverage["omitted_items"] += len(values) - len(retained)
        while retained:
            result[key] = retained
            if _size(result) <= MAX_BYTES - 128:
                return
            retained = retained[1:]
            coverage["omitted_items"] += 1
        result[key] = []

    try:
        contract = context.get("registered_entry_contract")
        if valid_record(contract, sid) and contract["origin"]["registered_at"] > now:
            return dict(version=1, status="UNAVAILABLE", reason="future_contract")
        if valid_record(contract, sid) and contract["origin"]["registered_at"] <= now:
            add("original_contract", contract)
        else:
            result["original_contract_status"] = "UNAVAILABLE"
        row = conn.execute(
            "SELECT id,substr(payload,1,?),status FROM llm_scenario_intents "
            "WHERE scenario_id=? ORDER BY rowid LIMIT 1", (_ROW_BYTES + 1, sid)).fetchone()
        if row:
            original = _object(row[1])
            if (original.get("action") != "OPEN" or original.get("scenario_id") != sid
                    or original.get("action_id") != row[0]
                    or (contract and valid_record(contract, sid)
                        and contract["origin"]["action_id"] != row[0])):
                raise ValueError("original_plan_binding")
            add("original_requested_plan", dict(source="REGISTERED_INTENT_NOT_FILL",
                intent_status_at_read=row[2], plan=_plan(original)))
        else:
            result["original_plan_status"] = "MISSING"
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError):
        coverage["invalid_rows"] += 1
        result["status"] = "PARTIAL"

    # Reuse evidence already checked by the broker; do not recompute quantities.
    thesis = context.get("entry_thesis")
    if (isinstance(thesis, dict) and thesis.get("status") == "VERIFIED"
            and _number(thesis.get("observed_at")) and thesis["observed_at"] <= now):
        add("initial_fill_evidence", _pick(thesis, ("status", "side", "initial_hard_stop",
            "initial_filled_quantity", "initial_fill_average", "first_fill_at",
            "latest_entry_fill_at", "observed_at", "scope")), 1500)
    else:
        result["initial_fill_status"] = "UNAVAILABLE"
    result["current_context_references"] = dict(
        observations=["positions", "target_status", "previous_hard_stop", "account_captured_at"],
        economics="runner_economics", advisory_checkpoints_not_exit_rules="review_memory")

    transitions, changes, observations = [], [], []
    previous_status = previous_observation = None
    try:
        # Project only needed context fields in SQLite before crossing the DB
        # boundary; snapshots and full prompts are intentionally never fetched.
        # SQLite 3.26 JSON serialization rounds scalar doubles. Return numeric
        # columns directly, and parse preserved JSON array text only in Python.
        rows = conn.execute("""
            SELECT slot,json_valid(context),
                CASE WHEN json_valid(context) THEN CASE
                    WHEN length(json_extract(context,'$.scenario_id'))<=128
                    THEN json_extract(context,'$.scenario_id') END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.now') IN ('integer','real') THEN json_extract(context,'$.now')
                    WHEN json_type(context,'$.now') IS NULL OR json_type(context,'$.now')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.input_captured_at') IN ('integer','real')
                    THEN json_extract(context,'$.input_captured_at')
                    WHEN json_type(context,'$.input_captured_at') IS NULL OR json_type(context,'$.input_captured_at')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.account_captured_at') IN ('integer','real')
                    THEN json_extract(context,'$.account_captured_at')
                    WHEN json_type(context,'$.account_captured_at') IS NULL OR json_type(context,'$.account_captured_at')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.mark_price') IN ('integer','real') THEN json_extract(context,'$.mark_price')
                    WHEN json_type(context,'$.mark_price') IS NULL OR json_type(context,'$.mark_price')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN substr(json_extract(context,'$.positions'),1,?) END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.previous_hard_stop') IN ('integer','real')
                    THEN json_extract(context,'$.previous_hard_stop')
                    WHEN json_type(context,'$.previous_hard_stop') IS NULL OR json_type(context,'$.previous_hard_stop')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN substr(json_extract(context,'$.target_status'),1,?) END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN length(json_extract(context,'$.accounting_status'))<=128
                    THEN json_extract(context,'$.accounting_status') END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.recorded_scenario_net') IN ('integer','real')
                    THEN json_extract(context,'$.recorded_scenario_net')
                    WHEN json_type(context,'$.recorded_scenario_net') IS NULL OR json_type(context,'$.recorded_scenario_net')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                substr(proposal,1,?),substr(outcome,1,?)
            FROM llm_scenario_decisions WHERE slot<? AND outcome IS NOT NULL
            ORDER BY slot DESC LIMIT ?
            """, (_ROW_BYTES + 1, _ROW_BYTES + 1, _ROW_BYTES + 1, _ROW_BYTES + 1,
                  int(now // 300), SCAN_LIMIT + 1)).fetchall()
        coverage["older_history"] = "OMITTED_SCAN_LIMIT" if len(rows) > SCAN_LIMIT else "NO_OLDER_STORED_ROWS"
        rows = rows[:SCAN_LIMIT]
        coverage["scanned_rows"] = len(rows)
        coverage["source_first_slot"] = rows[-1][0] if rows else None
        coverage["source_last_slot"] = rows[0][0] if rows else None
        for row in reversed(rows):
            try:
                slot, context_valid, *values = row
                if context_valid != 1:
                    raise ValueError("invalid_history_context")
                ctx = dict(zip(_CONTEXT_FIELDS, values[:-2]))
                for key in ("now", "input_captured_at", "account_captured_at", "mark_price",
                            "previous_hard_stop", "recorded_scenario_net"):
                    if ctx[key] is not None and not _number(ctx[key]):
                        raise ValueError("invalid_history_number")
                for key in ("positions", "target_status"):
                    raw = ctx[key]
                    if raw is not None:
                        if not isinstance(raw, str) or len(raw.encode()) > _ROW_BYTES:
                            raise ValueError("history_array_size")
                        ctx[key] = json.loads(raw)
                        if not isinstance(ctx[key], list):
                            raise ValueError("invalid_history_array")
                if _size(ctx) > _ROW_BYTES:
                    raise ValueError("history_context_size")
                raw_proposal, raw_outcome = values[-2:]
                outcome = _object(raw_outcome)
                if ctx.get("scenario_id") != sid:
                    continue
                coverage["same_scenario_rows"] += 1
                if outcome.get("status") not in {"wait", "intent_pending"}:
                    coverage["rejected_rows"] += 1
                    continue
                for key in ("now", "input_captured_at", "account_captured_at"):
                    stamp = ctx.get(key)
                    if stamp is not None and (not _number(stamp) or stamp > now):
                        raise ValueError("future_or_invalid_history_time")
                proposal = _object(raw_proposal)
                if proposal.get("scenario_id") != sid:
                    raise ValueError("proposal_binding")
                action = proposal.get("action")
                if outcome["status"] == "wait" and action != "WAIT":
                    raise ValueError("wait_binding")
                if outcome["status"] == "intent_pending":
                    intent = conn.execute(
                        "SELECT substr(payload,1,?),status FROM llm_scenario_intents WHERE id=? AND scenario_id=?",
                        (_ROW_BYTES + 1, proposal.get("action_id"), sid)).fetchone()
                    if not intent:
                        raise ValueError("missing_intent")
                    plan = _object(intent[0])
                    if (plan.get("scenario_id") != sid or plan.get("action_id") != proposal.get("action_id")
                            or plan.get("action") != action):
                        raise ValueError("intent_binding")
                    changes.append(dict(slot=slot, source="REQUESTED_PLAN_NOT_FILL",
                        outcome_at_decision=outcome["status"], intent_status_at_read=intent[1],
                        plan=_plan(plan)))
                assessment = proposal.get("thesis_assessment")
                if assessment is not None:
                    if (not isinstance(assessment, dict) or not valid_record(contract, sid)
                            or assessment.get("contract_id") != contract["contract_id"]
                            or assessment.get("status") not in {"INTACT", "WEAKENING", "FAILED", "UNKNOWN"}
                            or not isinstance(assessment.get("reason"), str)
                            or not assessment["reason"].strip()
                            or not 1 <= len(assessment["reason"]) <= 240):
                        raise ValueError("assessment_binding")
                    if assessment["status"] != previous_status:
                        transitions.append(dict(slot=slot, source="HISTORICAL_MODEL_OPINION",
                            **_pick(assessment, ("status", "reason"))))
                    previous_status = assessment["status"]
                observation = _observation(ctx, now)
                comparison = {k: v for k, v in observation.items() if k not in {"captured_at", "mark_price"}}
                if observation.get("status") != "UNAVAILABLE" and comparison != previous_observation:
                    observations.append(dict(slot=slot, **observation))
                    previous_observation = comparison
            except (ValueError, TypeError, KeyError, OverflowError, sqlite3.Error):
                coverage["invalid_rows"] += 1
    except (sqlite3.Error, ValueError, TypeError, OverflowError):
        coverage["invalid_rows"] += 1
        coverage["older_history"] = "UNAVAILABLE"
    coverage.update(status_transitions_found=len(transitions), requested_changes_found=len(changes),
                    observed_changes_found=len(observations))
    recent("thesis_transitions", transitions, 4)
    recent("requested_changes", changes, 3)
    recent("historical_observation_changes", observations, 3)
    if coverage["omitted_items"] or coverage["invalid_rows"] or coverage["older_history"] != "NO_OLDER_STORED_ROWS":
        result["status"] = "PARTIAL"
    return result
