"""Order-free, flat-only research: model prices, host arithmetic, model selection.

Not a runtime proposer. Results are research receipts, never executable orders.
Caller supplies an audited, no-retry generator and a durable research recorder.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
import time

from core.llm_scenario import ScenarioValidationError, risk_snapshot, validate_scenario
from core.scenario_limit_prices import validate_execution_prices
from engine.scenario_ma_context import build_ma_structure_context
from live import scenario_llm
from live.scenario_contract import _matches, _object, response_schema, validate_wire_proposal
from live.scenario_model_input import project_model_input
from live.scenario_runtime import snapshot_input_time


DRAFT_PROMPT = """
Order-free candidate research. This top-level research response contract replaces
the normal single-proposal output shape and overrides preceding instructions to
personally compute costs/reward/risk. All trading safety rules remain unchanged.
Return wait_proposal (a normal WAIT wire proposal), zero to two independent normal
OPEN wire candidates, and no_candidate_reason. Choose strategic direction, entry,
SL, TP, quantities and fractions yourself from supplied market evidence; the host
does not choose these. Candidates are mutually exclusive alternatives, never a
combined portfolio. Do not calculate reward/risk, costs or projected PnL yourself:
the host will calculate your exact proposals for subsequent comparison. Do not
shrink a structural SL to make size fit. A candidate may fail host risk validation;
it will remain visible but cannot be selected. If no grounded candidate exists,
return an empty candidates list, category NO_STRUCTURAL_SETUP and a qualitative
reason identifying missing structure or evidence, not invented arithmetic.
Economic objections alone must submit a candidate for host calculation, not use
NO_STRUCTURAL_SETUP to bypass calculation. Nonempty candidates require category
CANDIDATES_PROVIDED. First recovery observation stays WAIT only, empty candidates
and category FIRST_OBSERVATION. Candidate submission is not an order or permission
to trade. Unknown real fees never authorize invented net-reward arithmetic.
"""

SELECT_PROMPT = """
Order-free calculated candidate review. This top-level research response contract
replaces the normal single-proposal output shape and overrides preceding requests
for model arithmetic, never safety constraints. Choose an eligible candidate
ID and its exact receipt_sha256, or WAIT and the WAIT receipt_sha256. Never rewrite
prices, quantities, expiry or confidence; revised plans require a new research
run, not an extra call here. Interpret trend, support/resistance, fill plausibility
and supplied host calculations. Do not invent net profit or recompute supplied
arithmetic. Risk reserves are NOT observed per-side fees or guaranteed losses.
Gross target projections assume all proposed entries fill: a partial target is
not whole-scenario profit, and unknown future fees/funding remain unknown.
Unknown actual future fees alone are not a new automatic WAIT gate; evaluate the
supplied reserve-based comparisons without inventing exact net profit or an edge.
Pure wire/economic validation is NOT recovery authorization or execution evidence.
No trade is mandatory. Explain the decisive evidence and invalidation briefly in
Korean. Return exactly selection, receipt_sha256, rationale, no other fields.
"""


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":"))


def receipt_hash(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _decimal(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("missing_calculation_input")
    return Decimal(str(value))


def _gross_projection(plan, context):
    """All-entry-fill hypothetical arithmetic, not exchange allocation evidence."""
    entries = plan["entries"]
    quantity = sum((_decimal(row["quantity"]) for row in entries), Decimal(0))
    notional = sum((_decimal(row["price"]) * _decimal(row["quantity"])
                    for row in entries), Decimal(0))
    basis = notional / quantity
    sign = Decimal(1 if plan["side"] == "LONG" else -1)
    equity = _decimal(context["initial_equity"])
    step = _decimal(context["quantity_step"])
    minimum = _decimal(context["minimum_quantity"])
    if min(step, minimum, equity) <= 0:
        raise ValueError("invalid_calculation_input")
    targets = []
    risk_per_btc = _decimal(plan["risk"]["remaining_risk"]) / quantity
    for kind in ("take_profits", "partial_stops"):
        allocated = Decimal(0)
        for row in plan[kind]:
            # Same initial allocation formula as execution: no target fills yet.
            qty = (max(Decimal(0), min(quantity - allocated,
                   quantity * _decimal(row["fraction"]))) / step).to_integral_value(
                       rounding=ROUND_DOWN) * step
            allocated += qty
            below_minimum = qty < minimum
            projected_qty = Decimal(0) if below_minimum else qty
            gross = sign * (_decimal(row["price"]) - basis) * projected_qty
            targets.append(dict(kind=kind, id=row["id"], price=row["price"],
                quantity=float(projected_qty), below_minimum=below_minimum,
                gross_pnl=float(gross), equity_percent=float(gross / equity * 100),
                gross_reward_per_btc=float(sign * (_decimal(row["price"]) - basis)),
                gross_reward_to_reserved_risk_ratio=float(sign * (_decimal(row["price"]) - basis) / risk_per_btc)
                    if risk_per_btc > 0 else None))
    stop_gross = sign * (_decimal(plan["hard_stop"]) * quantity - notional)
    return dict(assumption="HYPOTHETICAL_ALL_ENTRIES_FILL_AT_LIMIT_NO_PRIOR_EXITS",
                quantity=float(quantity), weighted_entry=float(basis),
                notional=float(notional), gross_at_full_stop=float(stop_gross),
                gross_at_full_stop_equity_percent=float(stop_gross / equity * 100),
                targets=targets, target_paths="INDEPENDENT_NOT_ADDITIVE_WITH_FULL_STOP",
                weighted_risk_per_btc=float(risk_per_btc),
                ratio_semantics="GROSS_PRICE_REWARD_OVER_HOST_RESERVED_RISK_NOT_NET_EXPECTANCY",
                actual_future_fees=None, actual_future_funding=None,
                actual_net_pnl=None)


def _entry_risk_components(plan, context):
    stop = _decimal(plan["hard_stop"])
    cost = _decimal(context["estimated_cost_rate"])
    slip = _decimal(context["slippage_bps"]) / 10000
    sign = Decimal(1 if plan["side"] == "LONG" else -1)
    rows = []
    for row in plan["entries"]:
        price, quantity = _decimal(row["price"]), _decimal(row["quantity"])
        unit = risk_snapshot(initial_equity=context["initial_equity"], side=plan["side"],
            hard_stop=plan["hard_stop"], positions=[], pending_entries=[],
            entries=[dict(price=row["price"], quantity=1)], realized_loss=0,
            fees_paid=0, funding_paid=0, estimated_cost_rate=context["estimated_cost_rate"],
            slippage_bps=context["slippage_bps"])["proposed_risk"]
        rows.append(dict(id=row["id"], quantity=row["quantity"], price=row["price"],
            stop_distance_per_btc=float(max(Decimal(0), sign * (price - stop))),
            cost_reserve_per_btc=float(price * cost), stop_slippage_reserve_per_btc=float(stop * slip),
            risk_per_btc=unit, quantity_risk=float(quantity * Decimal(str(unit)))))
    return rows


def _supplied_instrument_filters(plan, context):
    """Subset available in saved context; not exchange preflight/authorization."""
    tick, step, minimum, notional, mark = (_decimal(context[key]) for key in
        ("price_tick", "quantity_step", "minimum_quantity", "minimum_notional", "mark_price"))
    if min(tick, step, minimum, notional, mark) <= 0:
        raise ValueError("invalid_instrument_filters")
    stop = _decimal(plan["hard_stop"])
    if stop % tick:
        raise ValueError("stop_off_tick")
    if (plan["side"] == "LONG" and stop >= mark) or (plan["side"] == "SHORT" and stop <= mark):
        raise ValueError("stop_crossed_market")
    for kind in ("entries", "take_profits", "partial_stops"):
        for row in plan[kind]:
            price = _decimal(row["price"])
            if price % tick:
                raise ValueError("price_off_tick")
            if kind == "entries":
                quantity = _decimal(row["quantity"])
                if quantity % step:
                    raise ValueError("quantity_off_step")
                if quantity < minimum or quantity * price < notional:
                    raise ValueError("entry_below_minimum")


def calculate_candidate(wire, context, *, candidate_id):
    """Return rejected candidates too; never repair model prices or quantities."""
    if (context.get("scenario_id") is not None or context.get("positions") != []
            or context.get("pending_entries") != []):
        raise ValueError("research_flat_only")
    raw = deepcopy(wire)
    receipt = dict(id=candidate_id, raw_proposal=raw, raw_sha256=receipt_hash(raw),
                   context_sha256=receipt_hash(context),
                   eligible=False, validation_scope="PURE_WIRE_ECONOMIC_AND_SUPPLIED_FILTERS_ONLY",
                   execution_authorized=False,
                   cost_semantics=dict(estimated_cost_rate=context.get("estimated_cost_rate"),
                       estimated_cost_rate_meaning="ONE_REMAINING_ENTRY_EXIT_FUNDING_RISK_RESERVE_NOT_PER_SIDE_FEE",
                       slippage_bps=context.get("slippage_bps"),
                       slippage_meaning="HARD_STOP_RISK_RESERVE_NOT_KNOWN_TP_EXECUTION_COST",
                       actual_future_fees=None, actual_future_funding=None))
    try:
        normalized = validate_wire_proposal(raw, context)
        if normalized["action"] != "OPEN":
            raise ValueError("candidate_not_open")
        core = {key: value for key, value in normalized.items()
                if key not in {"review", "recovery"}}
        # Compute raw risk before validation so an oversized candidate is visible.
        receipt["raw_risk"] = risk_snapshot(initial_equity=context["initial_equity"],
            side=core["side"], hard_stop=core["hard_stop"], positions=[], pending_entries=[],
            entries=core["entries"], risk_fraction=context.get("scenario_risk_fraction", .02),
            **{key: context[key] for key in ("realized_loss", "fees_paid", "funding_paid",
                                            "estimated_cost_rate", "slippage_bps")})
        receipt["raw_per_entry_risk"] = _entry_risk_components(core, context)
        _supplied_instrument_filters(core, context)
        effective = validate_execution_prices(core, context)
        _supplied_instrument_filters(effective, context)
        receipt.update(effective_plan=effective, effective_sha256=receipt_hash(effective),
            effective_per_entry_risk=_entry_risk_components(effective, context),
            projection=_gross_projection(effective, context), eligible=True)
    except ScenarioValidationError as exc:
        receipt["rejection"] = str(exc)
    except ValueError as exc:
        # Local wire/calculator validators emit fixed codes, not input values.
        receipt["rejection"] = str(exc)
    except (KeyError, TypeError, ArithmeticError):
        receipt["rejection"] = "wire_economic_or_calculation_invalid"
    receipt["receipt_sha256"] = receipt_hash(receipt)
    return receipt


def _model_payload(snapshot, context, response_contract):
    model_context = deepcopy(context)
    model_context["ma_structure"] = build_ma_structure_context(snapshot, context)
    model_context["current_primary_frame_facts"] = scenario_llm._current_primary_frame_facts(snapshot)
    for field in ("recent_waits", "recent_actions"):
        if isinstance(context.get(field), list):
            model_context[field] = [{key: value for key, value in row.items() if key != "rationale"}
                if isinstance(row, dict) else row for row in context[field]]
    return project_model_input(dict(market_snapshot=snapshot, contract_context=model_context,
                                   response_contract=response_contract))[0]


def research_review(snapshot, context, response_contract, *, generate, record,
                    clock=time.time):
    """At most two no-retry calls, original 75-second budget; never an order API.

    record(event_dict) must durably preserve evidence or raise. Historical callers
    supply a clock mapped to that observation, including actual elapsed latency.
    """
    from live.scenario_recovery import _wire
    if _wire.get() is not None:
        raise ValueError("production_wire_capture_forbidden")
    if not callable(generate) or not callable(record):
        raise ValueError("explicit_research_dependencies_required")
    if (context.get("scenario_id") is not None or context.get("positions") != []
            or context.get("pending_entries") != []):
        raise ValueError("research_flat_only")
    started = clock()
    original = receipt_hash(dict(snapshot=snapshot, context=context,
                                 response_contract=response_contract))
    frozen_snapshot, frozen_context, contract = deepcopy((snapshot, context, response_contract))

    def fresh():
        now = clock()
        if not 0 <= now - started < scenario_llm.TIMEOUT_SECONDS:
            raise ValueError("research_deadline")
        if frozen_snapshot.get("valid") is not True:
            raise ValueError("snapshot_unavailable")
        max_age = frozen_context.get("max_input_age_seconds", 120)
        if type(max_age) not in (int, float) or not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("research_invalid_input_age")
        for stamp in (snapshot_input_time(frozen_snapshot),
                      frozen_context.get("input_captured_at"),
                      frozen_context.get("account_captured_at")):
            if type(stamp) not in (float, int) or not math.isfinite(stamp) or not 0 <= now - stamp <= min(120, max_age):
                raise ValueError("research_stale_input_or_account")
        if receipt_hash(dict(snapshot=snapshot, context=context, response_contract=response_contract)) != original:
            raise ValueError("research_input_mutated")
        return now

    base = scenario_llm.SYSTEM_PROMPT + scenario_llm.MA_STRUCTURE_PROMPT + scenario_llm.FLAT_ENTRY_PROMPT
    fresh()
    payload = _model_payload(frozen_snapshot, frozen_context, contract)
    schema = response_schema(frozen_context)
    wait_schema = deepcopy(schema)
    wait_schema["properties"]["action"]["enum"] = ["WAIT"]
    candidate_schema = deepcopy(schema)
    candidate_schema["properties"]["action"]["enum"] = ["OPEN"]
    first_observation = schema["properties"]["action"]["enum"] == ["WAIT"]
    draft_schema = _object(dict(wait_proposal=wait_schema,
        candidates=dict(type="array", maxItems=0 if schema["properties"]["action"]["enum"] == ["WAIT"] else 2,
                        items=candidate_schema), no_candidate_reason=dict(type="string"),
        no_candidate_category=dict(type="string", enum=["FIRST_OBSERVATION", "NO_STRUCTURAL_SETUP", "CANDIDATES_PROVIDED"])))

    def call(phase, appendix, data, output_schema):
        now = fresh()
        prompt = _encoded(data)
        if len(prompt.encode()) > 100_000:
            raise ValueError("research_input_size")
        request = dict(system_prompt=base + appendix, user_prompt=prompt,
            model=scenario_llm.MODEL, reasoning_effort=scenario_llm.EFFORT,
            fast_tier=True, timeout=scenario_llm.TIMEOUT_SECONDS - (now - started),
            mcp_profile=None, response_schema=output_schema, retry_transport=False)
        request_hash = receipt_hash(request)
        record(deepcopy(dict(kind="MODEL_REQUEST", phase=phase, request=request,
                             request_sha256=request_hash, started_at=now)))
        actual_started = fresh()
        request["timeout"] = scenario_llm.TIMEOUT_SECONDS - (actual_started - started)
        actual_request_hash = receipt_hash(request)
        # No retry, fallback model or logical repair on any failure.
        try:
            result = generate(**deepcopy(request))
        except Exception:
            record(deepcopy(dict(kind="MODEL_ERROR", phase=phase, code="model_call_failed",
                request_sha256=request_hash, prepared_request_sha256=request_hash, actual_request=request,
                actual_request_sha256=actual_request_hash, elapsed_seconds=clock() - actual_started)))
            raise ValueError("research_model_call_failed") from None
        record(deepcopy(dict(kind="MODEL_RAW", phase=phase, request_sha256=request_hash,
                             prepared_request_sha256=request_hash,
                             actual_request=request, actual_request_sha256=actual_request_hash,
                             raw=result.text, elapsed_seconds=clock() - actual_started)))
        fresh()
        parsed = scenario_llm.parse_proposal(result.text)
        if not _matches(parsed, output_schema):
            raise ValueError("research_response_contract")
        return parsed

    draft = call("candidate", DRAFT_PROMPT, dict(payload, research_response_contract=draft_schema), draft_schema)
    expected_category = "CANDIDATES_PROVIDED" if draft["candidates"] else "FIRST_OBSERVATION" if first_observation else "NO_STRUCTURAL_SETUP"
    if draft["no_candidate_category"] != expected_category:
        raise ValueError("research_candidate_category")
    if len(draft["no_candidate_reason"]) > 2000 or (not draft["candidates"] and not draft["no_candidate_reason"].strip()):
        raise ValueError("research_missing_candidate_reason")
    now_context = dict(frozen_context, now=fresh())
    wait_wire = draft["wait_proposal"]
    wait_normalized = validate_wire_proposal(wait_wire, now_context)
    validate_scenario({k: v for k, v in wait_normalized.items() if k not in {"review", "recovery"}}, now_context)
    wait_receipt = dict(id="WAIT", raw_proposal=wait_wire, input_sha256=original)
    wait_receipt["receipt_sha256"] = receipt_hash(wait_receipt)
    receipts = [calculate_candidate(wire, now_context, candidate_id=f"c{index}")
                for index, wire in enumerate(draft["candidates"])]
    for receipt in receipts:
        receipt["input_sha256"] = original
        del receipt["receipt_sha256"]
        receipt["receipt_sha256"] = receipt_hash(receipt)
    bundle = dict(wait=wait_receipt, candidates=receipts, input_sha256=original,
                  no_candidate_category=draft["no_candidate_category"],
                  no_candidate_reason=draft["no_candidate_reason"],
                  qualitative_reason_truth="NOT_MECHANICALLY_VERIFIED")
    bundle_sha = receipt_hash(bundle)
    record(deepcopy(dict(kind="CALCULATED_CANDIDATES", body=bundle, sha256=bundle_sha)))
    if receipts:
        selection_schema = _object(dict(selection=dict(type="string", enum=["WAIT"] + [r["id"] for r in receipts if r["eligible"]]),
            receipt_sha256=dict(type="string"), rationale=dict(type="string")))
        final = call("selection", SELECT_PROMPT, dict(payload, calculated_candidates=bundle,
                     research_response_contract=selection_schema), selection_schema)
    else:
        final = dict(selection="WAIT", receipt_sha256=wait_receipt["receipt_sha256"],
                     rationale=draft["no_candidate_reason"])
    fresh()
    if receipt_hash(bundle) != bundle_sha:
        raise ValueError("research_candidate_mutated")
    selected = next((r for r in [wait_receipt] + receipts if r["id"] == final["selection"]), None)
    if selected is None or final["receipt_sha256"] != selected["receipt_sha256"]:
        raise ValueError("research_selection_hash")
    if not isinstance(final["rationale"], str) or not 1 <= len(final["rationale"].strip()) <= 2000:
        raise ValueError("research_selection_rationale")
    if selected["raw_proposal"]["expires_at"] <= fresh():
        raise ValueError("research_selected_expired")
    result = dict(mode="ORDER_FREE_CALCULATED_REVIEW", executable=False, orders_submitted=0,
                  calls=2 if receipts else 1, selection=final, selected=selected,
                  candidates=bundle, bundle_sha256=bundle_sha,
                  elapsed_seconds=clock() - started,
                  recovery_authorization="NOT_EVALUATED", fresh_execution_validation="NOT_PERFORMED")
    record(deepcopy(dict(kind="RESEARCH_RESULT", pending_post_record_time_check=True, body=result)))
    if selected["raw_proposal"]["expires_at"] <= fresh():
        raise ValueError("research_selected_expired")
    return deepcopy(result)
