from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from live.scenario_calculated_review import calculate_candidate, research_review
from live.scenario_contract import identity_fields
from live.scenario_preview import response_contract


def context():
    return dict(now=1000, input_id="research-1", input_captured_at=1000,
        account_captured_at=1000, max_input_age_seconds=120, scenario_id=None, revision=0,
        seen_action_ids=[], initial_equity=10000, positions=[], pending_entries=[],
        previous_hard_stop=None, realized_loss=0, fees_paid=0, funding_paid=0,
        estimated_cost_rate=.002, slippage_bps=20, new_risk_blocked=False,
        mark_price=83410, price_tick=.1, quantity_step=.001, minimum_quantity=.001,
        minimum_notional=5)


def wire(ctx, side=None):
    result = dict(**identity_fields(ctx), action="OPEN" if side else "WAIT",
        confidence=.7, expires_at=1200, rationale="가격 흐름과 무효화 근거",
        leverage=10, entries=[dict(id="e", price=83410, quantity=.01)] if side else [],
        take_profits=[dict(id="tp", price=84000 if side == "LONG" else 82500, fraction=.5)] if side else [],
        partial_stops=[], cancel_entry_ids=[], side=side,
        hard_stop=82900 if side == "LONG" else 84000 if side else None,
        chase=dict(max_bps=0, max_reprices=0) if side else None)
    if ctx.get("recovery_contract_version") in (1, 2):
        result["recovery"] = None
    return result


def run(ctx=None, candidates=None, *, mutate=None, clock=lambda: 1000, fail_second=False):
    ctx = ctx or context()
    candidates = [wire(ctx, "LONG")] if candidates is None else candidates
    events, calls = [], []
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            result = dict(wait_proposal=wire(ctx), candidates=candidates,
                          no_candidate_category="CANDIDATES_PROVIDED" if candidates else "FIRST_OBSERVATION"
                          if (ctx.get("recovery") or {}).get("first_observation") else "NO_STRUCTURAL_SETUP",
                          no_candidate_reason="가격 구조 근거 부족" if not candidates else "")
        else:
            if fail_second:
                raise RuntimeError("secret")
            data = json.loads(kwargs["user_prompt"])["calculated_candidates"]
            chosen = next((r for r in data["candidates"] if r["eligible"]), data["wait"])
            result = dict(selection=chosen["id"], receipt_sha256=chosen["receipt_sha256"],
                          rationale="계산과 추세 검토 후 선택")
        if mutate:
            mutate(result, len(calls), kwargs)
        return SimpleNamespace(text=json.dumps(result))
    result = research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
        generate=generate, record=events.append, clock=clock)
    return result, events, calls


def test_known_842_62_risk_error_is_calculated_by_host():
    receipt = calculate_candidate(wire(context(), "LONG"), context(), candidate_id="c0")
    assert receipt["eligible"]
    assert receipt["raw_per_entry_risk"][0]["risk_per_btc"] == pytest.approx(842.62)
    assert receipt["raw_risk"]["proposed_risk"] == pytest.approx(8.4262)
    assert receipt["projection"]["actual_net_pnl"] is None
    assert receipt["projection"]["targets"][0]["gross_pnl"] == pytest.approx(2.95)


@pytest.mark.parametrize("side,stop_pnl,tp_pnl", [("LONG", -5.1, 2.95), ("SHORT", -5.9, 4.55)])
def test_long_short_gross_and_partial_quantity(side, stop_pnl, tp_pnl):
    receipt = calculate_candidate(wire(context(), side), context(), candidate_id="c0")
    assert receipt["eligible"]
    p = receipt["projection"]
    assert p["gross_at_full_stop"] == pytest.approx(stop_pnl)
    assert p["targets"][0]["quantity"] == .005
    assert p["targets"][0]["gross_pnl"] == pytest.approx(tp_pnl)
    assert "NOT_PER_SIDE_FEE" in receipt["cost_semantics"]["estimated_cost_rate_meaning"]


def test_round_buffer_both_raw_effective_prices_and_hashes_preserved():
    ctx = context()
    ctx.update(execution_price_policy=dict(version="round-limit-v1"),
               limit_price_quotes=dict(bid=83400, ask=83401, captured_at=1000))
    original = wire(ctx, "LONG")
    r = calculate_candidate(original, ctx, candidate_id="c0")
    assert r["raw_proposal"]["entries"][0]["price"] == 83410
    assert r["effective_plan"]["entries"][0]["price"] == 83417.3
    assert r["effective_plan"]["take_profits"][0]["price"] == 83992.7
    assert r["raw_sha256"] != r["effective_sha256"]
    assert original == wire(ctx, "LONG")


def test_two_phase_exact_selection_no_order_and_audit():
    result, events, calls = run()
    assert result["executable"] is False and result["orders_submitted"] == 0
    assert result["selected"]["raw_proposal"] == wire(context(), "LONG")
    assert result["calls"] == len(calls) == 2
    assert [(e["kind"], e.get("phase")) for e in events][:4] == [
        ("MODEL_REQUEST", "candidate"), ("MODEL_RAW", "candidate"),
        ("CALCULATED_CANDIDATES", None), ("MODEL_REQUEST", "selection")]
    assert all(c["retry_transport"] is False and c["timeout"] <= 75 for c in calls)
    assert all(c["model"] == "gpt-6-luna" and c["mcp_profile"] is None for c in calls)


def test_no_candidates_wait_is_one_call():
    result, _, calls = run(candidates=[])
    assert result["selection"]["selection"] == "WAIT"
    assert result["calls"] == len(calls) == 1


def test_rejected_risk_stays_visible_but_not_selectable():
    p = wire(context(), "LONG")
    p["entries"][0]["quantity"] = 1
    result, _, _ = run(candidates=[p])
    assert result["selection"]["selection"] == "WAIT"
    r = result["candidates"]["candidates"][0]
    assert r["raw_risk"]["proposed_risk"] == pytest.approx(842.62)
    assert not r["eligible"]


@pytest.mark.parametrize("field,value", [("selection", "invented"), ("receipt_sha256", "bad"),
                                          ("price", 84000), ("rationale", "")])
def test_selection_tamper_rejected(field, value):
    def mutate(result, phase, _):
        if phase == 2:
            result[field] = value
    with pytest.raises(ValueError):
        run(mutate=mutate)


def test_second_failure_does_not_fallback_or_return_first_proposal():
    with pytest.raises(ValueError, match="research_model_call_failed") as e:
        run(fail_second=True)
    assert "secret" not in str(e.value)


@pytest.mark.parametrize("field,value", [("account_captured_at", None), ("account_captured_at", 879),
    ("account_captured_at", 1001), ("input_captured_at", 879), ("positions", [{"quantity": 1}]),
    ("pending_entries", [{"quantity": 1}]), ("scenario_id", "active")])
def test_missing_stale_or_nonflat_rejects_without_call(field, value):
    ctx = context()
    ctx[field] = value
    with pytest.raises(ValueError):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, {},
            generate=lambda **kw: pytest.fail("must not call"), record=lambda e: None, clock=lambda: 1000)


def test_first_observation_wait_only_and_no_open_allowed():
    ctx = context()
    ctx.update(recovery_contract_version=2, recovery=dict(phase="OBSERVING", first_observation=True))
    p = wire(ctx, "LONG")
    p["recovery"] = None
    with pytest.raises(ValueError, match="research_response_contract"):
        run(ctx=ctx, candidates=[p])


def test_candidate_limit_and_no_candidate_reason():
    with pytest.raises(ValueError, match="research_response_contract"):
        run(candidates=[wire(context(), "LONG")] * 3)
    def mutate(result, phase, _):
        if phase == 1:
            result["no_candidate_reason"] = ""
    with pytest.raises(ValueError, match="research_missing_candidate_reason"):
        run(candidates=[], mutate=mutate)


def test_deadline_not_renewed_and_stale_account_between_calls():
    for elapsed in (76, 71):
        now = [1000.]
        ctx = context()
        if elapsed == 71:
            ctx["account_captured_at"] = 950
        def mutate(_result, phase, _kwargs):
            if phase == 1:
                now[0] += elapsed
        with pytest.raises(ValueError, match="research_deadline|research_stale"):
            run(ctx=ctx, mutate=mutate, clock=lambda: now[0])


def test_second_call_remaining_timeout_and_expiry():
    now = [1000.]
    def mutate(_result, phase, _kwargs):
        now[0] += 20
    result, _, calls = run(mutate=mutate, clock=lambda: now[0])
    assert calls[1]["timeout"] == 55
    assert result["elapsed_seconds"] == 40
    p = wire(context(), "LONG")
    p["expires_at"] = 1030
    now[0] = 1000
    with pytest.raises(ValueError, match="research_selected_expired"):
        run(candidates=[p], mutate=mutate, clock=lambda: now[0])


@pytest.mark.parametrize("mutator", [lambda p: p["entries"][0].update(price=83410.01),
    lambda p: p["entries"][0].update(quantity=.0001), lambda p: p.update(hard_stop=85000),
    lambda p: p.update(hard_stop=float("nan"))])
def test_invalid_candidate_fails_closed(mutator):
    p = wire(context(), "LONG")
    mutator(p)
    try:
        r = calculate_candidate(p, context(), candidate_id="c0")
    except ValueError:
        return
    assert not r["eligible"]


def test_missing_step_is_not_assumed_and_below_minimum_tp_is_zero_not_rounded_up():
    ctx = context()
    del ctx["quantity_step"]
    assert not calculate_candidate(wire(context(), "LONG"), ctx, candidate_id="c0")["eligible"]
    p = wire(context(), "LONG")
    p["take_profits"][0]["fraction"] = .01
    target = calculate_candidate(p, context(), candidate_id="c0")["projection"]["targets"][0]
    assert target["quantity"] == 0 and target["below_minimum"]


def test_input_size_mutation_and_production_capture_rejected():
    ctx = context()
    ctx["large"] = "x" * 100001
    with pytest.raises(ValueError, match="research_input_size"):
        run(ctx=ctx)
    ctx = context()
    def mutate(_result, _phase, _kwargs):
        ctx["initial_equity"] = 99999
    with pytest.raises(ValueError, match="research_input_mutated"):
        run(ctx=ctx, mutate=mutate)
    from live.scenario_recovery import _wire
    token = _wire.set((None, None, None))
    try:
        with pytest.raises(ValueError, match="production_wire_capture_forbidden"):
            run()
    finally:
        _wire.reset(token)


def test_recorder_cannot_mutate_selected_receipts():
    events = []
    ctx = context()
    def record(event):
        events.append(deepcopy(event))
        if event["kind"] == "CALCULATED_CANDIDATES":
            event["body"]["candidates"][0]["raw_proposal"]["hard_stop"] = 1
    def generate(**kw):
        data = json.loads(kw["user_prompt"])
        if "calculated_candidates" not in data:
            answer = dict(wait_proposal=wire(ctx), candidates=[wire(ctx, "LONG")], no_candidate_reason="",
                          no_candidate_category="CANDIDATES_PROVIDED")
        else:
            c = data["calculated_candidates"]["candidates"][0]
            answer = dict(selection="c0", receipt_sha256=c["receipt_sha256"], rationale="판단 근거")
        return SimpleNamespace(text=json.dumps(answer))
    result = research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
        generate=generate, record=record, clock=lambda: 1000)
    assert result["selected"]["raw_proposal"]["hard_stop"] == 82900


def test_empty_category_cannot_claim_economic_rejection_or_false_first_observation():
    for category in ("ECONOMIC_REJECTION", "FIRST_OBSERVATION", "CANDIDATES_PROVIDED"):
        def mutate(result, _phase, _kwargs):
            result["no_candidate_category"] = category
        with pytest.raises(ValueError):
            run(candidates=[], mutate=mutate)
    ctx = context()
    ctx.update(recovery_contract_version=2, recovery=dict(phase="OBSERVING", first_observation=True))
    result, _, _ = run(ctx=ctx, candidates=[])
    assert result["candidates"]["no_candidate_category"] == "FIRST_OBSERVATION"


def test_per_btc_reward_and_ratio_are_host_calculated():
    r = calculate_candidate(wire(context(), "LONG"), context(), candidate_id="c0")
    unit = r["effective_per_entry_risk"][0]
    assert unit["stop_distance_per_btc"] == 510
    assert unit["cost_reserve_per_btc"] == 166.82
    assert unit["stop_slippage_reserve_per_btc"] == 165.8
    assert unit["risk_per_btc"] == pytest.approx(842.62)
    target = r["projection"]["targets"][0]
    assert target["gross_reward_per_btc"] == 590
    assert target["gross_reward_to_reserved_risk_ratio"] == pytest.approx(590 / 842.62)


def test_calculator_nonflat_rejects_and_rejected_risk_preserves_cost_semantics():
    ctx = context()
    ctx["positions"] = [dict(price=83410, quantity=.01)]
    with pytest.raises(ValueError, match="research_flat_only"):
        calculate_candidate(wire(ctx, "LONG"), ctx, candidate_id="c0")
    p = wire(context(), "LONG")
    p["entries"][0]["quantity"] = 1
    r = calculate_candidate(p, context(), candidate_id="c0")
    assert r["rejection"] == "risk budget exceeded"
    assert r["cost_semantics"]["actual_future_fees"] is None


def test_slow_recording_reduces_sent_timeout_and_blocks_late_final_result():
    now = [1000.]
    events, calls = [], []
    ctx = context()
    def record(event):
        events.append(event)
        if event["kind"] == "MODEL_REQUEST":
            now[0] += 10
        if event["kind"] == "RESEARCH_RESULT":
            now[0] += 80
    def generate(**kw):
        calls.append(kw)
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wire(ctx), candidates=[],
            no_candidate_category="NO_STRUCTURAL_SETUP", no_candidate_reason="구조 근거 부족")))
    with pytest.raises(ValueError, match="research_deadline"):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
            generate=generate, record=record, clock=lambda: now[0])
    assert calls[0]["timeout"] == 65
    raw = next(e for e in events if e["kind"] == "MODEL_RAW")
    assert raw["actual_request"] == calls[0]
    assert raw["prepared_request_sha256"] != raw["actual_request_sha256"]
    assert events[-1]["pending_post_record_time_check"]


def test_receipt_hash_binds_snapshot_even_when_input_id_and_plan_are_same():
    ctx = context()
    hashes = []
    def generate(**kw):
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wire(ctx), candidates=[],
            no_candidate_category="NO_STRUCTURAL_SETUP", no_candidate_reason="구조 근거 부족")))
    for snapshot in (dict(valid=True, as_of_ms=1000000), dict(valid=True, as_of_ms=1000000, extra=1)):
        result = research_review(snapshot, ctx, response_contract(ctx), generate=generate,
                                 record=lambda e: None, clock=lambda: 1000)
        hashes.append(result["selected"]["receipt_sha256"])
    assert hashes[0] != hashes[1]


@pytest.mark.parametrize("raw", ["x" * 24001, '{"a":1,"a":2}', '{"a":NaN}', '[]'])
def test_bad_or_oversize_raw_response_never_triggers_second_call(raw):
    calls = []
    ctx = context()
    def generate(**kw):
        calls.append(kw)
        return SimpleNamespace(text=raw)
    with pytest.raises(ValueError):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
            generate=generate, record=lambda e: None, clock=lambda: 1000)
    assert len(calls) == 1


def test_failed_required_request_record_prevents_any_model_call():
    def record(_event):
        raise OSError("disk unavailable")
    ctx = context()
    with pytest.raises(OSError):
        research_review(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
            generate=lambda **kw: pytest.fail("unrecorded call"), record=record, clock=lambda: 1000)


def test_both_directions_can_be_proposed_without_code_choosing_prices():
    proposals = [wire(context(), "LONG"), wire(context(), "SHORT")]
    result, _, _ = run(candidates=proposals)
    assert [r["raw_proposal"] for r in result["candidates"]["candidates"]] == proposals
    assert all(r["eligible"] for r in result["candidates"]["candidates"])


def test_minimum_notional_is_preserved():
    ctx = context()
    ctx["minimum_notional"] = 1000
    result = calculate_candidate(wire(ctx, "LONG"), ctx, candidate_id="c0")
    assert not result["eligible"] and result["rejection"] == "entry_below_minimum"


def test_original_stricter_input_age_not_extended():
    ctx = context()
    ctx["max_input_age_seconds"] = 30
    now = [1000]
    def mutate(_result, _phase, _kwargs):
        now[0] += 20
    with pytest.raises(ValueError, match="research_stale"):
        run(ctx=ctx, mutate=mutate, clock=lambda: now[0])
