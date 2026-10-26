"""Strict arm provenance and independent Decimal accounting; no network."""
import copy

import pytest

from tests.test_scenario_replay_comparison import comparison, fixture, registry_for, save


def registered(root):
    variants = [fixture(root), fixture(root, name="B")]
    for name in ("A", "B"):
        for mode in ("fresh", "frozen"):
            folder = root / name / mode
            report = comparison.read(folder / "report.json")
            contract = report["contract"]
            common = {key: "pinned" for key in comparison.CALCULATED_COMMON_FIELDS}
            common.update(contract)
            common.update(schema=4, initial_state={"profile": "fresh-unblocked"},
                          economic_clock_contract="NATIVE_MONOTONIC_INTEGER_MS_FULL_TICK_V1",
                          identity_scheme="EXACT_REPLAY_UUID5_MODULE_CALLSITE_SEQUENCE_V1",
                          observed_data={"mode": "observed", "provider_manifest": {
                              "packet_sha256": "packet", "schedule_sha256": "schedule",
                              "input_coverage_eligible": True, "missing_slots": 0, "allow_gaps": False}})
            common.update(policy_profile="single-pass-economic-v4" if name == "A" else "calculated-economic-v4",
                          runtime_flags={"review": name == "B"}, context_contracts={"version": 4},
                          assembled_prompt_hashes={"active": name + "prompt"}, prompt_hash=name + "system",
                          review_binding=None if name == "A" else {"policy": "review-pin"},
                          phase_contracts={"SINGLE_PASS" if name == "A" else "DRAFT": name + "schema"})
            report["contract"] = common
            exchange = comparison.read(folder / "exchange.json")
            for execution, trade, fee in zip(exchange["executions"], exchange["transactions"], (".11", ".0605")):
                execution.update(isMaker=False, execFee=fee)
                trade.update(fee=fee, change=str(comparison.number(trade["cashFlow"]) - comparison.number(fee)))
            exchange["transactions"][-1]["qty"] = "1"
            exchange.update(cash="1010.3295", equity="1014.3295")
            report["economic"].update(final_equity=1014.3295, net_change=14.3295,
                                       fees=.1705, terminal_mark_price="104")
            report.update(execution_status="COMPLETE", failure_evidence=None,
                          model_failures=0, input_preparation_failures=0, protection_outcomes={},
                          coverage=dict(planned_slots=1, processed_slots=1, eligible=True,
                                        model_free_drain_status="COMPLETE", model_free_drain_end_ms=300000,
                                        final_economic_time_ms=300000))
            save(folder / "exchange.json", exchange)
            save(folder / "equity.json", [[0, 1000], [15, 990], [300000, 1014.3295]])
            seal(folder, report)
    registry = registry_for(root, variants)
    contracts = {name: comparison.read(root / name / "fresh/report.json")["contract"] for name in ("A", "B")}
    registry.update(schema=2, contract_comparison={
        "common_fields": list(comparison.CALCULATED_COMMON_FIELDS),
        "arm_fields": list(comparison.CALCULATED_ARM_FIELDS),
        "variant_fields": list(comparison.CALCULATED_VARIANT_FIELDS),
        "expected_common_contract": {key: contracts["A"][key] for key in comparison.CALCULATED_COMMON_FIELDS},
        "expected_arm_contracts": {name: {key: contract[key] for key in comparison.CALCULATED_ARM_FIELDS}
                                   for name, contract in contracts.items()},
        "expected_variant_contracts": {name: {key: contract[key] for key in comparison.CALCULATED_VARIANT_FIELDS}
                                       for name, contract in contracts.items()},
    }, economic_oracle_inputs={"terminal_mark_price": "104", "funding_events": [
        {"timestamp_ms": 30, "rate": "-.005", "mark_price": "100"}]})
    save(root / "registry.json", registry)
    return registry


def seal(folder, report):
    report["contract_hash"] = comparison.digest(report["contract"])
    report["exchange_hash"] = comparison.digest(comparison.read(folder / "exchange.json"))
    report["result_hash"] = comparison.digest({key: value for key, value in report.items()
                                              if key not in {"result_hash", "execution_metadata"}})
    save(folder / "report.json", report)


def test_exact_registered_arm_differences_with_shared_observed_inputs(tmp_path):
    registered(tmp_path)
    result = comparison.compare_calculated(tmp_path)
    assert result["comparison_complete"]
    assert result["arms"][0]["accounting"]["net_equity_change"] == "14.3295"
    assert result["unrealized_basis"] == "DECIMAL_POSITION_TIMES_TERMINAL_MARK_MINUS_AVERAGE"
    assert not comparison.compare(tmp_path)["comparison_complete"]  # no silent schema upgrade


@pytest.mark.parametrize("key", ["policy_profile", "runtime_flags", "context_contracts", "assembled_prompt_hashes",
                                 "prompt_hash", "review_binding", "phase_contracts", "source_hashes", "observed_data",
                                 "costs", "initial_state", "economic_clock_contract", "unknown"])
def test_rehashed_unknown_or_unregistered_contract_mutations_rejected(tmp_path, key):
    registered(tmp_path)
    folder = tmp_path / "B/fresh"
    report = comparison.read(folder / "report.json")
    report["contract"][key] = "tampered"
    seal(folder, report)
    result = comparison.compare_calculated(tmp_path)
    assert result["arms"][1]["status"] == "INVALID"
    assert not result["comparison_complete"]


@pytest.mark.parametrize("field", ["common_fields", "arm_fields", "variant_fields"])
def test_registry_cannot_broaden_field_allowlist(tmp_path, field):
    registry = registered(tmp_path)
    registry["contract_comparison"][field].append("anything")
    save(tmp_path / "registry.json", registry)
    assert all(row["status"] == "INVALID" for row in comparison.compare_calculated(tmp_path)["arms"])


def test_both_arms_same_forged_source_manifest_cannot_pass_external_pin(tmp_path):
    registry = registered(tmp_path)
    registry["contract_comparison"]["expected_common_contract"]["source_hashes"] = {"a": "forged"}
    for name in ("A", "B"):
        folder = tmp_path / name / "fresh"
        report = comparison.read(folder / "report.json")
        report["contract"]["source_hashes"] = {"a": "forged"}
        seal(folder, report)
    save(tmp_path / "registry.json", registry)
    assert all(row["error"] == "registry_sources_mismatch" for row in comparison.compare_calculated(tmp_path)["arms"])


@pytest.mark.parametrize("mutation", ["omitted_funding", "duplicate_funding", "wrong_fee", "zero_fee", "wrong_mark", "wrong_funding"])
def test_decimal_oracle_rejects_self_consistent_fake_economics(tmp_path, mutation):
    registry = registered(tmp_path)
    folder = tmp_path / "A/fresh"
    report = comparison.read(folder / "report.json")
    exchange = comparison.read(folder / "exchange.json")
    if mutation == "omitted_funding":
        exchange["transactions"].pop()
    elif mutation == "duplicate_funding":
        exchange["transactions"].append(copy.deepcopy(exchange["transactions"][-1]))
    elif mutation in {"wrong_fee", "zero_fee"}:
        exchange["executions"][0]["execFee"] = "0" if mutation == "zero_fee" else "1"
    elif mutation == "wrong_mark":
        report["economic"]["terminal_mark_price"] = "105"
    else:
        exchange["transactions"][-1]["funding"] = "1"
    with pytest.raises(ValueError):
        comparison.verify_calculated_economics(report, exchange, registry)


def test_funding_oracle_handles_short_and_settlement_before_same_time_fill(tmp_path):
    registry = registered(tmp_path)
    folder = tmp_path / "A/fresh"
    report = comparison.read(folder / "report.json")
    exchange = comparison.read(folder / "exchange.json")
    exchange["executions"][0]["side"] = "Sell"
    exchange["executions"][1]["side"] = "Buy"
    exchange["executions"][1]["execTime"] = "30"
    exchange["transactions"][1]["cashFlow"] = "-10"
    exchange["transactions"][-1].update(funding="-1", qty="2")
    exchange.update(position="-1", cash="988.8295", equity="984.8295")
    comparison.verify_calculated_economics(report, exchange, registry)


def test_no_funding_schedule_requires_no_settlement_and_keeps_nonzero_costs(tmp_path):
    registry = registered(tmp_path)
    folder = tmp_path / "A/fresh"
    report = comparison.read(folder / "report.json")
    exchange = comparison.read(folder / "exchange.json")
    registry["economic_oracle_inputs"]["funding_events"] = []
    with pytest.raises(ValueError, match="funding_schedule_coverage"):
        comparison.verify_calculated_economics(report, exchange, registry)
    exchange["transactions"].pop()
    exchange.update(cash="1009.8295", equity="1013.8295")
    comparison.verify_calculated_economics(report, exchange, registry)


def test_funding_schedule_cannot_duplicate_or_skip_inventory_timestamp(tmp_path):
    registry = registered(tmp_path)
    folder = tmp_path / "A/fresh"
    report = comparison.read(folder / "report.json")
    exchange = comparison.read(folder / "exchange.json")
    event = registry["economic_oracle_inputs"]["funding_events"][0]
    registry["economic_oracle_inputs"]["funding_events"].append(copy.deepcopy(event))
    with pytest.raises(ValueError, match="funding_schedule_time_order"):
        comparison.verify_calculated_economics(report, exchange, registry)


def test_stopped_last_slot_flat_full_coverage_matching_replay_is_diagnostic_only(tmp_path):
    registered(tmp_path)
    for mode in ("fresh", "frozen"):
        folder = tmp_path / "A" / mode
        report = comparison.read(folder / "report.json")
        exchange = comparison.read(folder / "exchange.json")
        exchange["executions"].append(dict(execId="three", orderId="order3", side="Sell",
            execQty="1", execPrice="104", execFee=".0572", execTime="50", isMaker=False))
        exchange["transactions"].append(dict(id="tthree", type="TRADE", tradeId="three",
            orderId="order3", transactionTime="50", fee=".0572", funding="0", cashFlow="4", change="3.9428"))
        exchange.update(position="0", average_price="0", cash="1014.2723", equity="1014.2723")
        report["economic"].update(final_equity=1014.2723, net_change=14.2723, fees=.2277, open_quantity=0)
        report.update(execution_status="STOPPED", model_failures=1, failure_evidence=dict(
            status="STOPPED_TECHNICAL_FAILURE", reason="ScenarioModelError", slot_ms=0,
            remaining_decision_slots=0, protect_only_end_ms=300000, new_model_calls_forbidden=True))
        report["coverage"]["eligible"] = False
        report["readiness"]["insufficiency_reasons"].append("STOPPED_TECHNICAL_FAILURE")
        save(folder / "exchange.json", exchange)
        save(folder / "equity.json", [[0, 1000], [15, 990], [300000, 1014.2723]])
        seal(folder, report)
    result = comparison.compare_calculated(tmp_path)
    row = result["arms"][0]
    assert row["status"] == "INVALID" and row["diagnostic_only"] is True
    assert row["unverified_reported_metrics"]["open_quantity"] == 0
    assert row["coverage"]["planned_slots"] == row["coverage"]["processed_slots"]
    assert row["failure_evidence"]["remaining_decision_slots"] == 0
    assert not result["comparison_complete"]


@pytest.mark.parametrize("mutation", ["stopped_without_evidence", "complete_with_evidence", "eligible_false",
    "eligible_integer", "planned", "processed", "drain_status", "drain_end", "final_time",
    "model_error", "input_error", "hidden_warning", "protection_error", "missing_coverage", "extra_coverage"])
def test_rehashed_completion_flags_failure_evidence_crosschecks_fail_closed(tmp_path, mutation):
    registered(tmp_path)
    for mode in ("fresh", "frozen"):
        folder = tmp_path / "A" / mode
        report = comparison.read(folder / "report.json")
        if mutation == "stopped_without_evidence":
            report["execution_status"] = "STOPPED"
        elif mutation == "complete_with_evidence":
            report["failure_evidence"] = {"reason": "model_error"}
        elif mutation in {"eligible_false", "eligible_integer"}:
            report["coverage"]["eligible"] = False if mutation == "eligible_false" else 1
        elif mutation in {"planned", "processed"}:
            report["coverage"][mutation + "_slots"] = 0
        elif mutation == "drain_status":
            report["coverage"]["model_free_drain_status"] = "INCOMPLETE_PROTECTION_OR_SETTLEMENT"
        elif mutation == "drain_end":
            report["coverage"]["model_free_drain_end_ms"] = 299999
        elif mutation == "final_time":
            report["coverage"]["final_economic_time_ms"] = 299999
        elif mutation == "model_error":
            report["model_failures"] = 1
        elif mutation == "input_error":
            report["input_preparation_failures"] = 1
        elif mutation == "hidden_warning":
            report["readiness"]["insufficiency_reasons"].append("STOPPED_TECHNICAL_FAILURE")
        elif mutation == "protection_error":
            report["protection_outcomes"]["failed"] = 1
        elif mutation == "missing_coverage":
            del report["coverage"]
        else:
            report["coverage"]["ignore_error"] = True
        seal(folder, report)
    result = comparison.compare_calculated(tmp_path)
    assert result["arms"][0]["status"] == "INVALID"
    assert result["arms"][0]["diagnostic_only"]
    assert not result["comparison_complete"]


def test_goal_profile_cannot_pin_legacy_process_identity_as_new_contract(tmp_path):
    registry = registered(tmp_path)
    contract = comparison.read(tmp_path/'A/fresh/report.json')['contract']
    contract['policy_profile'] = 'economic-reject-goal-v2'
    registry['contract_comparison']['expected_arm_contracts']['A']['policy_profile'] = contract['policy_profile']
    with pytest.raises(ValueError, match='economic_identity_contract_mismatch'):
        comparison.verify_calculated_registry_contract(contract, registry, registry['variants'][0])
