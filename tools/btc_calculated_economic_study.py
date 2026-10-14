"""Explicit prepare/run for one preregistered four-slot development smoke.

No stage-two expansion, retry, restart, orders, or profitability approval.
"""
# Standalone CLI must add the hyphenated package directory before imports.
# ruff: noqa: E402
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "prism-btc"))

from analysis.scenario_dataset import load_bundle, digest
from analysis.scenario_replay import contract_for, run_replay, network_boundary
from backtest.observed_scenario_data import ObservedScenarioData
from tools.btc_research_call_guard import CallGuard
from tools.btc_research_model_call import GuardedModelCall
from tools.compare_btc_scenario_replays import (
    CALCULATED_COMMON_FIELDS, CALCULATED_ARM_FIELDS, CALCULATED_VARIANT_FIELDS,
    compare_calculated, verify_run, verify_calculated_registry_contract,
    verify_calculated_economics, verify_calculated_completion,
)

ORDER = ("A_OHLC", "B_OHLC", "B_OLHC", "A_OLHC")
MAX_HTTP = 24


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def source_manifest():
    paths = [p for p in (ROOT / "prism-btc").rglob("*.py")
             if "tests" not in p.relative_to(ROOT / "prism-btc").parts]
    paths += [ROOT / "tools" / name for name in (
        "btc_calculated_economic_study.py", "btc_research_call_guard.py",
        "btc_research_model_call.py", "compare_btc_scenario_replays.py")]
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(paths)}


def _oracle(market, bundle):
    def previous_mark(at):
        rows = market.mark.loc[market.mark.index.view("i8") // 1_000_000 == at - 60000]
        require(len(rows) == 1, "missing_previous_confirmed_mark")
        return str(float(rows.iloc[0].close))
    start, end = bundle["start_ms"], bundle["end_ms"]
    return dict(terminal_mark_price=previous_mark(end), funding_events=[
        dict(timestamp_ms=row["timestamp"], rate=str(row["rate"]), mark_price=previous_mark(row["timestamp"]))
        for row in bundle["funding"] if start < row["timestamp"] <= end])


def prepare(root, market_path, observed_path, observed_sha256, source_reference, *, initial_equity=10000.):
    """Freeze original files and first four slots before any model is constructed."""
    root, market_path, observed_path = map(lambda p: Path(p).resolve(), (root, market_path, observed_path))
    require(not root.exists(), "never_overwrite_study")
    require(isinstance(source_reference, str) and bool(source_reference), "source_reference_required")
    full, market = load_bundle(market_path)
    observed = ObservedScenarioData(observed_path, observed_sha256, allow_gaps=False)
    all_slots = observed.schedule()
    require(len(all_slots) >= 4, "four_observed_slots_required")
    start, end = all_slots[0]["slot"] * 300000, (all_slots[0]["slot"] + 4) * 300000
    require(full["start_ms"] <= start < end <= full["end_ms"], "market_window_not_contained")
    pilot = deepcopy(full)
    pilot.update(start_ms=start, end_ms=end)
    pilot["data_hash"] = digest({k: v for k, v in pilot.items() if k not in {"data_hash", "coverage", "provenance"}})
    variants, contracts = [], {}
    for name in ORDER:
        profile = "single-pass-economic-v4" if name.startswith("A_") else "calculated-economic-v4"
        path = name.split("_")[1]
        variants.append(dict(name=name, path=path, cost_multiplier=1, profile=profile))
        contracts[name] = contract_for(pilot, path, 1., initial_equity, origin="luna", policy_profile=profile,
            initial_state="recovery-stage0", observed_manifest=observed.manifest(),
            market_input_mode="observed", allow_input_gaps=False)
    first = contracts[ORDER[0]]
    common = {k: first[k] for k in CALCULATED_COMMON_FIELDS}
    require(all({k: c[k] for k in CALCULATED_COMMON_FIELDS} == common for c in contracts.values()),
            "prepared_common_contract_mismatch")
    def utc(at):
        return datetime.fromtimestamp(at / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    registry = dict(schema=2, stage=1, purpose="DEVELOPMENT_SMOKE_NOT_PROFITABILITY_PROOF",
        max_http_calls=MAX_HTTP, run_order=list(ORDER), slots=4, next_stage_automatic=False,
        latency_analysis="NATIVE_LATENCY_ONLY", initial_state="recovery-stage0",
        source_reference=source_reference, source_pin_reference=source_reference,
        source_root=str(ROOT), source_manifest=source_manifest(),
        original_inputs=dict(market_path=str(market_path), market_sha256=sha(market_path),
            observed_path=str(observed_path), observed_sha256=observed_sha256, full_data_hash=full["data_hash"]),
        pilot_data_hash=pilot["data_hash"], schedule=observed.schedule(start, end), variants=variants,
        start=utc(start), end=utc(end), initial_equity=initial_equity,
        model=first["model"], reasoning=first["effort"], service_tier="priority",
        expected_data_hash=pilot["data_hash"], expected_source_hashes_sha256=digest(first["source_hashes"]),
        expected_runtime_versions=first["runtime_versions"],
        funding_mark_basis="ASSUMED_PREVIOUS_CONFIRMED_MINUTE_MARK_NOT_EXCHANGE_EVENT_MARK",
        economic_oracle_inputs=_oracle(market, pilot),
        contract_comparison=dict(common_fields=list(CALCULATED_COMMON_FIELDS), arm_fields=list(CALCULATED_ARM_FIELDS),
            variant_fields=list(CALCULATED_VARIANT_FIELDS), expected_common_contract=common,
            expected_arm_contracts={name: {k: c[k] for k in CALCULATED_ARM_FIELDS} for name, c in contracts.items()},
            expected_variant_contracts={name: {k: c[k] for k in CALCULATED_VARIANT_FIELDS} for name, c in contracts.items()}))
    for variant in variants:
        verify_calculated_registry_contract(contracts[variant["name"]], registry, variant)
    root.mkdir(parents=True)
    write_new(root / "pilot.json", pilot)
    registry["pilot_file_sha256"] = sha(root / "pilot.json")
    write_new(root / "registry.json", registry)
    write_new(root / "registry.sha256.json", {"sha256": sha(root / "registry.json")})
    return registry


def _verify_pins(root, registry):
    require(sha(root / "registry.json") == json.loads((root / "registry.sha256.json").read_text())["sha256"],
            "registry_changed")
    require(registry["source_root"] == str(ROOT) and source_manifest() == registry["source_manifest"],
            "source_manifest_changed")
    inputs = registry["original_inputs"]
    require(sha(inputs["market_path"]) == inputs["market_sha256"]
            and sha(inputs["observed_path"]) == inputs["observed_sha256"]
            and sha(root / "pilot.json") == registry["pilot_file_sha256"], "frozen_data_changed")


def _verify_inputs(root, registry):
    _verify_pins(root, registry)
    inputs = registry["original_inputs"]
    pilot, market = load_bundle(root / "pilot.json")
    observed = ObservedScenarioData(inputs["observed_path"], inputs["observed_sha256"], allow_gaps=False)
    require(pilot["data_hash"] == registry["pilot_data_hash"]
            and observed.schedule(pilot["start_ms"], pilot["end_ms"]) == registry["schedule"]
            and _oracle(market, pilot) == registry["economic_oracle_inputs"], "frozen_window_changed")
    for variant in registry["variants"]:
        contract = contract_for(pilot, variant["path"], variant["cost_multiplier"], registry["initial_equity"],
            origin="luna", policy_profile=variant["profile"], initial_state=registry["initial_state"],
            observed_manifest=observed.manifest(), market_input_mode="observed", allow_input_gaps=False)
        verify_calculated_registry_contract(contract, registry, variant)
    return pilot, market


def run(root, *, replay=run_replay, model_factory=GuardedModelCall):
    """One explicit serial run. An existing start marker forbids all restarts."""
    root = Path(root).resolve()
    registry = json.loads((root / "registry.json").read_text())
    require(registry["stage"] == 1 and registry["max_http_calls"] == MAX_HTTP
            and registry["run_order"] == list(ORDER) and registry["slots"] == 4
            and registry["next_stage_automatic"] is False, "fixed_stage_one_required")
    write_new(root / "run_started.json", {"registry_sha256": sha(root / "registry.json")})
    guard = CallGuard(root / "call_guard", sha(root / "registry.json"), MAX_HTTP)
    arm_counts, dispatches, parity = {}, {}, {}
    comparison_status, diagnostic_record_status, coordinator_error = "AVAILABLE", "COMPLETE", None
    events_path = root / "model_events.jsonl"
    with events_path.open("x", encoding="utf-8") as events:
        def record(event):
            events.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
            events.flush()
            os.fsync(events.fileno())

        def before_http(_dispatch):
            try:
                _verify_pins(root, registry)
                require(not guard.snapshot()["stopped"], "global_model_calls_stopped")
            except BaseException:
                guard.stop("source_or_data_changed")
                raise

        def fail(evidence):
            guard.stop("technical_failure")
            record(dict(kind="STUDY_FAILURE", evidence=evidence))

        try:
            for variant in registry["variants"]:
                if guard.snapshot()["stopped"]:
                    break
                pilot, market = _verify_inputs(root, registry)
                name = variant["name"]
                arm = root / name
                arm.mkdir()
                tape = arm / "tape.jsonl"
                generator = model_factory(guard, name, record)
                kwargs = dict(path=variant["path"], cost_multiplier=variant["cost_multiplier"],
                    initial_equity=registry["initial_equity"], policy_profile=variant["profile"],
                    initial_state=registry["initial_state"], max_decisions=4,
                    observed_packet=registry["original_inputs"]["observed_path"],
                    observed_packet_sha256=registry["original_inputs"]["observed_sha256"],
                    market_input_mode="observed", allow_input_gaps=False)
                before_count = guard.snapshot()["actual_http_calls"]
                parity[name] = "MISSING_FRESH_REPORT_OR_TAPE"
                try:
                    with network_boundary("fresh", os.environ.get("PRISM_BTC_SCENARIO_OAUTH_URL")):
                        report = replay(pilot, market, arm / "fresh", mode="fresh", tape_path=tape,
                            model_generator=generator, before_http=before_http, failure_callback=fail, **kwargs)
                finally:
                    arm_counts[name] = guard.snapshot()["actual_http_calls"] - before_count
                dispatches[name] = report["execution_metadata"].get("model_generator_dispatches")
                parity[name] = "MISSING_FROZEN"
                pilot, market = _verify_inputs(root, registry)
                with network_boundary("frozen"):
                    frozen = replay(pilot, market, arm / "frozen", mode="frozen", tape_path=tape, **kwargs)
                require(report["result_hash"] == frozen["result_hash"], "fresh_frozen_result_mismatch")
                for produced in (report, frozen):
                    require(produced["result_hash"] == digest({k: v for k, v in produced.items()
                        if k not in {"result_hash", "execution_metadata"}}), "fresh_frozen_report_hash_mismatch")
                for filename in ("exchange.json", "equity.json"):
                    require(json.loads((arm / "fresh" / filename).read_text())
                            == json.loads((arm / "frozen" / filename).read_text()), "fresh_frozen_artifact_mismatch")
                require(frozen["execution_metadata"]["actual_model_calls"] == 0, "frozen_actual_http_calls")
                parity[name] = ("VERIFIED_FAILED_ZERO_MODEL_CALLS" if report["execution_status"] == "STOPPED"
                                else "VERIFIED_ZERO_MODEL_CALLS")
                require(report["execution_metadata"]["actual_model_calls"] == arm_counts[name],
                        "report_http_count_mismatch")
                require(report["execution_status"] == "COMPLETE" and report["failure_evidence"] is None,
                        "fresh_arm_stopped")
                for mode in ("fresh", "frozen"):
                    verified, exchange, _, _, _ = verify_run(arm / mode, variant, registry,
                        frozen=mode == "frozen", contract_verifier=verify_calculated_registry_contract)
                    verify_calculated_economics(verified, exchange, registry)
                    verify_calculated_completion(verified, exchange)
            comparison = compare_calculated(root)
            require(comparison["comparison_complete"], "stage_one_comparison_incomplete")
            require(sum(arm_counts.values()) == guard.snapshot()["actual_http_calls"], "study_http_count_mismatch")
            status = "COMPLETE_DEVELOPMENT_SMOKE"
        except BaseException as error:
            guard.stop("coordinator_failure")
            reason = str(error)
            if re.fullmatch("[a-z][a-z0-9_]{1,100}", reason) is None:
                reason = "coordinator_external_error"
            coordinator_error = dict(error_type=type(error).__name__, reason=reason)
            try:
                record(dict(kind="COORDINATOR_ERROR", **coordinator_error))
            except Exception:
                diagnostic_record_status = "FAILED"
            try:
                comparison = compare_calculated(root)
            except Exception:
                comparison, comparison_status = None, "UNAVAILABLE"
            status = "STOPPED"
        result = dict(status=status, call_guard=guard.snapshot(), comparison=comparison,
                      comparison_status=comparison_status, coordinator_error=coordinator_error,
                      diagnostic_record_status=diagnostic_record_status,
                      per_arm_actual_http_calls=arm_counts, per_arm_model_generator_dispatches=dispatches,
                      per_arm_frozen_parity=parity,
                      profitability_proven=False, automatic_promotion=False, next_stage_automatic=False)
        write_new(root / "study_result.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    prep = subs.add_parser("prepare")
    prep.add_argument("--root", type=Path, required=True)
    prep.add_argument("--market", type=Path, required=True)
    prep.add_argument("--observed", type=Path, required=True)
    prep.add_argument("--observed-sha256", required=True)
    prep.add_argument("--source-reference", required=True)
    prep.add_argument("--initial-equity", type=float, default=10000.)
    execute = subs.add_parser("run")
    execute.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.root, args.market, args.observed, args.observed_sha256,
                         args.source_reference, initial_equity=args.initial_equity)
        print(json.dumps(dict(status="PREPARED_ZERO_CALLS", registry_sha256=sha(args.root / "registry.json"),
                              registry_canonical_sha256=digest(result))))
    else:
        result = run(args.root)
        print(json.dumps(dict(status=result["status"], call_guard=result["call_guard"])))


if __name__ == "__main__":
    main()
