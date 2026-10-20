"""Immutable entry premise and sampled failure evidence (never continuous prices)."""
import copy
import hashlib
import json
import math


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _object(properties):
    return dict(type="object", properties=properties, required=list(properties), additionalProperties=False)


def entry_contract_schema(version=1):
    text = dict(type="string", minLength=1, maxLength=240)
    schema = _object(dict(version=dict(type="integer", enum=[version]), regime=dict(type="string", enum=["TREND", "BREAKOUT", "RANGE"]),
        premise=text, expected_progress=text,
        explicit_failure=_object(dict(source=dict(type="string", enum=["MARK_PRICE"]),
            operator=dict(type="string", enum=["ge", "le"]), price=dict(type="number", exclusiveMinimum=0),
            minimum_observations=dict(type="integer", enum=[2, 3])))))
    if version == 2:
        from live.scenario_entry_goal import goal_schema
        schema['properties'].pop('expected_progress')
        schema['properties']['goal'] = goal_schema()
        schema['required'] = list(schema['properties'])
    return schema


def _definition(value):
    version = value.get('version') if isinstance(value, dict) else None
    if type(version) is not int or version not in (1, 2):
        return False
    if set(value) != set(entry_contract_schema(version)["properties"]):
        return False
    if version == 2:
        from live.scenario_entry_goal import valid_goal
        if not valid_goal(value['goal']):
            return False
    failure = value.get("explicit_failure")
    return (type(value.get("version")) is int and value["version"] in (1, 2)
        and value.get("regime") in ("TREND", "BREAKOUT", "RANGE")
        and all(isinstance(value.get(k), str) and 1 <= len(value[k].strip()) <= 240
                for k in (("premise", "expected_progress") if version == 1 else ("premise",)))
        and isinstance(failure, dict) and set(failure) == {"source", "operator", "price", "minimum_observations"}
        and failure["source"] == "MARK_PRICE" and failure["operator"] in ("ge", "le")
        and _number(failure["price"]) and type(failure["minimum_observations"]) is int
        and failure["minimum_observations"] in (2, 3))


def _digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def bind_contract(definition, validated, registered_at, *, resolved_goal=None):
    if not _definition(definition) or not _number(registered_at) or validated.get("action") != "OPEN":
        raise ValueError("entry_contract_invalid_registration")
    # Execution-price normalization must not move the final entry across its
    # original failure boundary. Core execution metadata is irrelevant here.
    economic = {k: v for k, v in validated.items() if k != 'host_entry_contract_id'}
    validate_metadata(dict(economic, entry_contract=definition, thesis_assessment=None),
                      dict(entry_contract_version=definition['version'], scenario_id=None))
    result = dict(version=definition['version'], definition=copy.deepcopy(definition), origin=dict(
        scenario_id=validated["scenario_id"], action_id=validated["action_id"],
        input_id=validated["input_id"], registered_at=registered_at))
    if definition['version'] == 2:
        from live.scenario_entry_goal import valid_resolved, validate_goal_plan
        if (not valid_resolved(resolved_goal) or resolved_goal.get('goal') != definition['goal']
                or resolved_goal.get('input_id') != validated['input_id']
                or resolved_goal['snapshot_as_of_ms'] > registered_at * 1000):
            raise ValueError('entry_goal_registration_missing')
        validate_goal_plan(validated, resolved_goal)
        result['resolved_goal'] = copy.deepcopy(resolved_goal)
        result['origin']['plan_sha256'] = _digest(economic)
    return dict(result, contract_id=_digest(result))


def valid_record(record, scenario_id):
    try:
        if not isinstance(record, dict):
            return False
        keys = {"version", "definition", "origin", "contract_id"}
        if record.get('version') == 2:
            keys.add('resolved_goal')
        if (set(record) != keys or type(record["version"]) is not int
                or record["version"] not in (1, 2) or not _definition(record["definition"])
                or record['definition']['version'] != record['version']):
            return False
        if record['version'] == 2:
            from live.scenario_entry_goal import valid_resolved
            resolved = record['resolved_goal']
            if (not valid_resolved(resolved) or resolved.get('goal') != record['definition']['goal']
                    or resolved.get('input_id') != record['origin'].get('input_id')):
                return False
        origin = record["origin"]
        origin_keys = {"scenario_id", "action_id", "input_id", "registered_at"}
        if record['version'] == 2:
            origin_keys.add('plan_sha256')
        if (not isinstance(origin, dict) or set(origin) != origin_keys
                or origin["scenario_id"] != scenario_id or not _number(origin["registered_at"])
                or any(not isinstance(origin[k], str) or not origin[k] for k in ("scenario_id", "action_id", "input_id"))):
            return False
        if record['version'] == 2 and (not isinstance(origin['plan_sha256'], str)
                or len(origin['plan_sha256']) != 64 or any(c not in '0123456789abcdef' for c in origin['plan_sha256'])):
            return False
        if record['version'] == 2 and record['resolved_goal']['snapshot_as_of_ms'] > origin['registered_at'] * 1000:
            return False
        return record["contract_id"] == _digest({k: v for k, v in record.items() if k != "contract_id"})
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def contract_context(active, enabled):
    if not enabled:
        return {}
    record = active.get("entry_contract") if active else None
    valid = bool(active and valid_record(record, active.get("scenario_id")))
    prior = active.get("last_thesis_assessment") if valid else None
    previous = None
    if (isinstance(prior, dict) and prior.get("contract_id") == record["contract_id"]
            and prior.get("status") in ("INTACT", "WEAKENING", "FAILED", "UNKNOWN")
            and isinstance(prior.get("reason"), str) and prior["reason"].strip() and len(prior["reason"]) <= 240):
        at = active.get("last_thesis_assessment_at")
        previous = dict(contract_id=prior["contract_id"], status=prior["status"], reason=prior["reason"],
            assessed_at=at if _number(at) and at >= record["origin"]["registered_at"] else None,
            source="HISTORICAL_MODEL_OPINION")
    return dict(entry_contract_version=record['version'] if valid else 1, registered_entry_contract=copy.deepcopy(record) if valid else None,
        previous_thesis_assessment=previous,
        entry_contract_status="REGISTERED" if valid else "CORRUPT" if record is not None else "LEGACY_UNKNOWN",
        entry_contract_failure_rule=dict(copy.deepcopy(record["definition"]["explicit_failure"]),
            confirmation_scope="DISTINCT_CONSECUTIVE_FRESH_DECISION_SLOTS") if valid else None,
        entry_contract_observation=copy.deepcopy(active.get("entry_contract_observation")) if valid else None)


def goal_record_marker(record):
    origin = record.get('origin') if isinstance(record, dict) else None
    return (isinstance(record, dict) and (record.get('version') == 2 or 'resolved_goal' in record)
            or isinstance(origin, dict) and 'plan_sha256' in origin)


def has_goal_contract(conn, active):
    """A damaged version marker must not turn a v2 reservation into legacy."""
    if not active:
        return False
    record = active.get('entry_contract')
    origin = record.get('origin', {}) if isinstance(record, dict) else {}
    if goal_record_marker(record):
        return True
    policy = active.get('stop_policy')
    identifiers = [policy.get('initial_action_id') if isinstance(policy, dict) else None,
                   origin.get('action_id') if isinstance(origin, dict) else None]
    for ident in dict.fromkeys(i for i in identifiers if isinstance(i, str)):
        row = conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=? AND scenario_id=?',
                           (ident, active['scenario_id'])).fetchone()
        if row:
            try:
                plan = json.loads(row[0])
            except (ValueError, TypeError):
                continue  # No positive v2 evidence; preserve the existing base-trust fence.
            if isinstance(plan, dict) and 'host_entry_contract_id' in plan:
                return True
    return False


def registered_contract(conn, active):
    """Base failure-rule trust is independent of optional goal source retention."""
    if not active or not valid_record(active.get('entry_contract'), active.get('scenario_id')):
        return False
    record = active['entry_contract']
    row = conn.execute('SELECT body FROM llm_scenario_entry_contracts WHERE scenario_id=?',
                       (active['scenario_id'],)).fetchone()
    if not row or row[0] != json.dumps(record, sort_keys=True, allow_nan=False):
        return False
    original = conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=? AND scenario_id=?',
                            (record['origin']['action_id'], active['scenario_id'])).fetchone()
    if not original:
        return False
    try:
        plan = json.loads(original[0])
        if (plan.get('action') != 'OPEN' or plan.get('side') != active.get('side')
                or plan.get('scenario_id') != active['scenario_id']
                or plan.get('action_id') != record['origin']['action_id']
                or plan.get('input_id') != record['origin']['input_id']):
            return False
        if record['version'] == 2 or 'host_entry_contract_id' in plan:
            if record['version'] != 2 or plan.get('host_entry_contract_id') != record['contract_id']:
                return False
        return bind_contract(record['definition'], plan, record['origin']['registered_at'],
                             resolved_goal=record.get('resolved_goal')) == record
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def assessment_schema(context):
    record = context.get("registered_entry_contract")
    if not valid_record(record, context.get("scenario_id")):
        return dict(type="null")
    return _object(dict(contract_id=dict(type="string", enum=[record["contract_id"]]),
        status=dict(type="string", enum=["INTACT", "WEAKENING", "FAILED", "UNKNOWN"]),
        reason=dict(type="string", minLength=1, maxLength=240)))


def validate_metadata(payload, context, allow_failed_wait_template=False):
    if 'host_entry_contract_id' in payload:
        raise ValueError('entry_contract_host_id_forbidden')
    if context.get("entry_contract_version") not in (1, 2):
        if "entry_contract" in payload or "thesis_assessment" in payload:
            raise ValueError("entry_contract_not_enabled")
        return
    if not {"entry_contract", "thesis_assessment"} <= payload.keys():
        raise ValueError("entry_contract_metadata_missing")
    definition, assessment = payload["entry_contract"], payload["thesis_assessment"]
    if context.get('entry_goal_untrusted') and payload.get('action') in {'OPEN', 'ADJUST'} and (
            payload.get('entries') or payload.get('take_profits')):
        raise ValueError('entry_goal_untrusted_no_plan_replacement')
    if context.get("scenario_id") and context.get("entry_contract_status") in ("CORRUPT", "LEGACY_UNKNOWN") and payload.get("entries"):
        raise ValueError("entry_contract_corrupt_no_new_entries")
    if payload.get("action") == "OPEN":
        if not _definition(definition) or definition['version'] != context['entry_contract_version']:
            raise ValueError("entry_contract_definition_invalid")
        failure = definition["explicit_failure"]
        entries, stop = payload.get("entries", []), payload.get("hard_stop")
        prices = [e.get("price") for e in entries if isinstance(e, dict)]
        if not prices or not all(_number(p) for p in prices) or not _number(stop):
            raise ValueError("entry_contract_prices_invalid")
        threshold = failure["price"]
        if not ((payload.get("side") == "LONG" and failure["operator"] == "le" and stop <= threshold < min(prices))
                or (payload.get("side") == "SHORT" and failure["operator"] == "ge" and max(prices) < threshold <= stop)):
            raise ValueError("entry_contract_failure_direction")
    elif definition is not None:
        raise ValueError("entry_contract_replacement_forbidden")
    record = context.get("registered_entry_contract")
    if context.get("entry_contract_status") == "REGISTERED" and not valid_record(record, context.get("scenario_id")):
        raise ValueError("entry_contract_corrupt_context")
    if valid_record(record, context.get("scenario_id")):
        if (not isinstance(assessment, dict) or set(assessment) != {"contract_id", "status", "reason"}
                or assessment["contract_id"] != record["contract_id"]
                or assessment["status"] not in ("INTACT", "WEAKENING", "FAILED", "UNKNOWN")
                or not isinstance(assessment["reason"], str) or not 1 <= len(assessment["reason"].strip()) <= 240):
            raise ValueError("entry_contract_assessment_invalid")
        failed = assessment["status"] == "FAILED" or (context.get("entry_contract_observation") or {}).get("status") == "FAILED"
        if failed and payload.get("action") != "EXIT" and not (allow_failed_wait_template and payload.get("action") == "WAIT"):
            raise ValueError("entry_contract_failed_requires_exit")
    elif assessment is not None:
        raise ValueError("entry_contract_assessment_without_record")


def strip_metadata(payload):
    return {k: v for k, v in payload.items() if k not in {"entry_contract", "thesis_assessment", "host_entry_contract_id"}}


def observe_contract(active, ctx, slot, now):
    """Count distinct adjacent decision slots; duplicate calls cannot add evidence."""
    if not active or not valid_record(active.get("entry_contract"), active.get("scenario_id")):
        return
    record = active["entry_contract"]
    previous = active.get("entry_contract_observation")
    previous = previous if isinstance(previous, dict) else {}
    thesis = ctx.get("entry_thesis")
    thesis = thesis if isinstance(thesis, dict) else {}
    failure = record["definition"]["explicit_failure"]
    samples = previous.get("samples", []) if isinstance(previous, dict) else []
    had_samples = bool(samples)
    good = isinstance(samples, list) and 0 < len(samples) <= failure["minimum_observations"]
    if good:
        for index, sample in enumerate(samples):
            good = (isinstance(sample, dict) and set(sample) == {"slot", "captured_at", "mark_price", "account_version", "first_fill_at"}
                and type(sample["slot"]) is int and _number(sample["captured_at"])
                and int(sample["captured_at"] // 300) == sample["slot"] and _number(sample["mark_price"])
                and _number(now) and sample["captured_at"] <= now and sample["slot"] <= slot
                and isinstance(sample["account_version"], str) and bool(sample["account_version"])
                and _number(sample["first_fill_at"])
                and record["origin"]["registered_at"] <= sample["first_fill_at"] <= sample["captured_at"]
                and (sample["mark_price"] >= failure["price"] if failure["operator"] == "ge" else sample["mark_price"] <= failure["price"])
                and (index == 0 or (sample["slot"] == samples[index-1]["slot"]+1
                    and sample["first_fill_at"] == samples[index-1]["first_fill_at"])))
            if not good:
                break
    good = (good and previous.get("contract_id") == record["contract_id"]
        and type(previous.get("count")) is int and previous.get("count") == len(samples)
        and previous.get("slot") == samples[-1]["slot"]
        and previous.get("captured_at") == samples[-1]["captured_at"])
    samples = samples if good else []
    at, mark = ctx.get("account_captured_at"), ctx.get("mark_price")
    valid = (type(slot) is int and _number(now) and int(now // 300) == slot
        and _number(at) and 0 <= now-at <= 120 and int(at // 300) == slot
        and _number(mark) and isinstance(ctx.get("account_version"), str) and bool(ctx["account_version"])
        and ctx.get("scenario_id") == active["scenario_id"]
        and thesis.get("status") == "VERIFIED" and thesis.get("side") == active.get("side")
        and _number(thesis.get("first_fill_at")) and record["origin"]["registered_at"] <= thesis["first_fill_at"] <= at
        and thesis.get("observed_at") == at and thesis.get("current_mark") == mark)
    if had_samples and not good:
        valid = False
    if samples and _number(thesis.get("first_fill_at")) and thesis["first_fill_at"] != samples[0]["first_fill_at"]:
        samples = []
        valid = False  # Changed original-fill identity requires investigation, not a new count.
    if len(samples) >= failure["minimum_observations"]:
        # Historical failure stays latched. The runtime separately requires a
        # fresh account before execution; stale/missing current quotes cannot
        # erase previously verified samples or manufacture additional ones.
        previous["status"] = "FAILED"
        return
    observation = dict(contract_id=record["contract_id"], slot=slot, status="UNKNOWN", count=0,
        samples=[],
        scope="Distinct sampled decision observations, not continuous price persistence")
    if valid:
        if samples and at < samples[-1]["captured_at"]:
            return
        breached = mark >= failure["price"] if failure["operator"] == "ge" else mark <= failure["price"]
        sample = dict(slot=slot, captured_at=at, mark_price=mark, account_version=ctx["account_version"],
                      first_fill_at=thesis["first_fill_at"])
        if breached:
            if samples and samples[-1]["slot"] == slot:
                samples = samples[:-1] + [sample]
            elif samples and samples[-1]["slot"] == slot-1:
                samples = samples + [sample]
            else:
                samples = [sample]
        else:
            samples = []
        count = len(samples)
        observation.update(status="FAILED" if count >= failure["minimum_observations"] else "CONFIRMING" if breached else "INTACT",
            count=count, samples=samples, captured_at=at, mark_price=mark, account_version=ctx["account_version"])
    active["entry_contract_observation"] = observation
