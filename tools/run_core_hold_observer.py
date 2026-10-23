"""Core long-term holding observer, record only (prism_core/core_hold.py, H-1023-corehold).

Reads real KR/US positions (read-only database), fetches completed daily OHLC bars and records
what the core rule would do. It never orders, never blocks a sell and never writes a holding.

    python tools/run_core_hold_observer.py --market KR            # after the close (cron)
    python tools/run_core_hold_observer.py --market US --dry-run  # print only, no state/journal/event
    python tools/run_core_hold_observer.py --market US --backfill-since 2026-06-20 --out report.md

Variants, each with its own CAP-slot book:
- (a) as today: promotion only while really held (the trailing stop and other exits stay in force);
- (b) trailing stop exempt: positions sold within 40 sessions after the peak reached entry +5%
  follow the research engine from the sale (two closes under MA50 -> next open, -7% stop) and
  may be promoted then.
Every position is recomputed from bars on each run (a daily rule replays exactly), so the state
file only remembers statuses for change events. Outputs:
- runtime/core_hold_{kr,us}.json      positions[key] = {status, status_b, ...} (add side reads status)
- runtime/core_hold_journal_{kr,us}.jsonl   one line per position per run
- events core_hold.observed on a status change (trace_id = the position's trade trace)
Switch: CORE_HOLD_OBSERVER_ENABLED (default true; off = exit 0).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism_core import core_hold as C  # noqa: E402

_OPEN_SQL = {
    "KR": "SELECT ticker, company_name, buy_date, buy_price, scenario FROM stock_holdings",
    "US": "SELECT ticker, company_name, buy_date, buy_price, scenario FROM us_stock_holdings",
}
_CLOSED_SQL = {
    "KR": ("SELECT ticker, company_name, buy_date, buy_price, scenario, sell_date, sell_price, exit_kind "
           "FROM trading_history WHERE sell_date >= ? ORDER BY sell_date"),
    "US": ("SELECT ticker, company_name, buy_date, buy_price, scenario, sell_date, sell_price, exit_kind "
           "FROM us_trading_history WHERE sell_date >= ? ORDER BY sell_date"),
}
REGISTERED = "2026-10-23"             # H-1023-corehold registration: daily population starts here
HISTORY_PAD_DAYS = 340                # calendar days before the entry: MA200 + 20-session slope at session 40


def enabled():
    return os.getenv("CORE_HOLD_OBSERVER_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def db_path():
    return os.getenv("STOCK_TRACKING_DB") or str(ROOT / "stock_tracking_db.sqlite")


def load_positions(market, since, path=None):
    """Open holdings plus trades sold on/after ``since``, one per (ticker, buy minute)."""
    conn = sqlite3.connect(f"file:{path or db_path()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r, sell_date=None, sell_price=None, exit_kind=None) for r in conn.execute(_OPEN_SQL[market])]
        rows += [dict(r) for r in conn.execute(_CLOSED_SQL[market], (since,))]
    finally:
        conn.close()
    seen, out = set(), []
    for row in rows:
        key = f"{row['ticker']}|{str(row['buy_date'])[:16]}"
        if key not in seen:
            seen.add(key)
            out.append(dict(row, key=key))
    return out


def _frame_to_bars(frame):
    if frame is None or frame.empty:
        return [], None
    cols = {str(c).lower(): c for c in frame.columns}
    pick = {k: cols.get(k) or cols.get(ko) for k, ko in
            (("open", "시가"), ("high", "고가"), ("low", "저가"), ("close", "종가"))}
    if pick["close"] is None:
        return [], None
    bars = [{"date": idx.date().isoformat(), **{k: (float(row[c]) if c is not None else None) for k, c in pick.items()}}
            for idx, row in frame.iterrows()]
    splits = cols.get("stock splits")
    split_dates = None if splits is None else [idx.date().isoformat() for idx, v in frame[splits].items() if v]
    return bars, split_dates


def fetch_bars(market, ticker, start, end):
    """Daily OHLC (raw KR like the runner rule; US split-adjusted) and the corporate-action dates."""
    if market == "US":
        from prism_core.us_market_data import daily_history
        return _frame_to_bars(daily_history(ticker, start, end))
    from cores import market_data
    bars, _ = _frame_to_bars(market_data.get_market_ohlcv_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker, adjusted=False))
    flags = market_data.get_corporate_action_flags(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)
    if flags is None or flags.empty:
        return bars, None                                  # unknown, never "no event"
    return bars, [idx.date().isoformat() for idx, v in flags["Modified"].items() if str(v).upper() == "Y"]


def _session(market, stamp):
    from prism_core import runner_hold as R
    return R.entry_session(market, stamp).isoformat()


def _index_on_or_after(bars, day):
    return next((i for i, b in enumerate(bars) if str(b["date"]) >= str(day)), None)


def evaluate(position, market, bars, corporate_dates, *, today):
    """Pure evaluation of one position: promotion candidates for (a) and (b), exits, returns."""
    from prism_core import runner_hold as R
    from prism_core.sell_facts import average_entry
    first = R.entry_reference(position.get("scenario"), position.get("buy_price"))
    avg = average_entry(position.get("scenario"))
    entry = avg[0] if avg else first
    from prism_core.trace_ids import decision_of
    out = {"key": position["key"], "ticker": position["ticker"], "company": position.get("company_name"),
           "decision_id": decision_of(position.get("scenario")),
           "buy_date": position["buy_date"], "entry_avg": entry, "entry_first": first, "legs": avg[1] if avg else 1,
           "open": position.get("sell_date") is None, "a": None, "b": None}
    try:
        entry_session = _session(market, position["buy_date"])
    except Exception:  # noqa: BLE001 - a malformed buy date stays unknown
        out["error"] = "buy_date"
        return out
    out["entry_session"] = entry_session
    if not bars or not entry:
        out["error"] = "bars"
        return out
    start = _index_on_or_after(bars, entry_session)
    if start is None:
        out["error"] = "bars"
        return out
    sale_session = None if out["open"] else _session(market, position["sell_date"])
    out["sale_session"] = sale_session
    out["actual_exit"] = None if out["open"] else {"date": sale_session, "price": C._num(position.get("sell_price")),
                                                     "exit_kind": position.get("exit_kind")}
    held_until = len(bars) if out["open"] else (_index_on_or_after(bars, sale_session) or len(bars))
    latest = None
    for i in range(start, held_until):                 # (a) promotion only on closes while really held
        latest = C.promotion(bars, i, entry=entry, entry_session=entry_session)
        if latest["eligible"]:
            out["a"] = _candidate(bars, i, latest, entry, first)
            out["b"] = dict(out["a"], source="a")      # the exemption book holds every (a) promotion too
            break
    out["latest"] = latest
    if not out["open"] and out["a"] is None:
        sold = held_until if held_until < len(bars) else None
        sessions = None if sold is None else C.session_number(bars, sold, entry_session)
        armed = C.trailing_armed(bars, entry=first, entry_session=entry_session, until=sale_session)
        out["b_applies"] = (None if sessions is None or armed is None
                            else bool(sessions < C.MIN_SESSIONS and armed))
        if out["b_applies"]:
            kind, view = C.counterfactual_path(bars, start_index=sold, entry=entry, entry_session=entry_session,
                                               first_entry=first)
            if kind == "PROMOTED":
                out["b"] = _candidate(bars, view["index"], view, entry, first)
            engine = C.counterfactual_path(bars, start_index=sold, entry=entry, entry_session=entry_session,
                                           first_entry=first, promote=False)
            out["b_path"] = {"kind": kind, "exit": engine[1] if engine[0] == "EXIT" else None}
    out["corporate_action"] = _corporate(corporate_dates, bars, out)
    out["last_close"] = {"date": bars[-1]["date"], "close": C._num(bars[-1].get("close"))}
    return out


def _candidate(bars, index, view, entry, first):
    share, ret = C.rank_inputs(bars, index)
    return {"date": view["date"], "index": index, "view": {k: view[k] for k in ("sessions", "gain", "close", "ma50",
                                                                                 "ma200", "ma200_lag")},
            "share": share, "ret": ret,
            "exit": C.core_exit(bars, start_index=index, entry=entry),
            "exit_first_basis": C.core_exit(bars, start_index=index, entry=first)}


def _corporate(dates, bars, out):
    """True when a split/share issue fell inside the MA200 window (200 + 20 sessions) of the promotion
    (or of the latest bar) or after it; None when unknown (the warning is per position)."""
    if dates is None:
        return None
    cand = out.get("a") or out.get("b")
    at = cand["index"] if cand else len(bars) - 1
    first_day = bars[max(0, at - (200 + C.SLOPE_LAG - 1))]["date"]
    return any(d >= first_day for d in dates)


def allocate(results, variant, cap=C.CAP):
    """Give each variant's candidates a slot in promotion-date order (no replacement)."""
    cands = [r for r in results if r.get(variant)]
    taken = []                                          # (exit date or None)
    for day in sorted({r[variant]["date"] for r in cands}):
        same = [r for r in cands if r[variant]["date"] == day]
        ordered = C.rank([{"ticker": r["ticker"], "share": r[variant]["share"], "ret": r[variant]["ret"], "r": r}
                          for r in same])
        taken = [t for t in taken if t is None or t > day]
        for item in ordered:
            r = item["r"]
            if len(taken) < cap:
                exit_ = r[variant]["exit"]
                taken.append(exit_["date"] if exit_ else None)
                r[variant]["slot"] = True
            else:
                r[variant]["slot"] = False
    return results


def status(result, variant):
    if result.get("error"):
        return "UNKNOWN"
    cand = result.get(variant)
    if cand is not None:
        if not cand.get("slot"):
            return "CAP_SKIPPED"
        return "CORE_EXITED" if cand["exit"] else "CORE"
    if variant == "a" or result["open"]:
        return "WATCH" if result["open"] else "SOLD_BEFORE_PROMOTION"
    if result.get("b_applies") is None:
        return "UNKNOWN"
    if not result["b_applies"]:
        return "NOT_APPLICABLE"
    return "CF_EXITED" if (result.get("b_path") or {}).get("exit") else "CF_WATCH"


def outcome(result, variant):
    """(actual return, rule return, rule still open) on the average entry; None when unknown."""
    entry = C._num(result.get("entry_avg"))
    actual = result.get("actual_exit")
    last = result["last_close"]["close"] if result.get("last_close") else None
    actual_ret = (actual["price"] / entry - 1) if actual and actual.get("price") and entry else (
        (last / entry - 1) if result["open"] and last and entry else None)
    cand = result.get(variant)
    if variant == "b" and result.get("b_applies") and not (cand and cand.get("slot")):
        exit_ = (result.get("b_path") or {}).get("exit")       # exempt, no core slot: the engine exit
        if exit_ and exit_.get("price") and entry:
            return actual_ret, exit_["price"] / entry - 1, False
        return actual_ret, (last / entry - 1) if last and entry else None, True
    if not (cand and cand.get("slot")):
        return actual_ret, actual_ret, result["open"]
    exit_ = cand["exit"]
    if exit_:
        return actual_ret, exit_["price"] / entry - 1, False
    return actual_ret, (last / entry - 1) if last and entry else None, True


def _stats(values):
    vals = [v for v in values if v is not None]
    wins, losses = [v for v in vals if v > 0], [v for v in vals if v <= 0]
    avg_w = sum(wins) / len(wins) if wins else None
    avg_l = sum(losses) / len(losses) if losses else None
    return {"n": len(vals), "win_rate": len(wins) / len(vals) if vals else None, "avg_win": avg_w, "avg_loss": avg_l,
            "payoff": (avg_w / abs(avg_l)) if avg_w is not None and avg_l else None}


def _pct(v):
    return "MISSING" if v is None else f"{v * 100:+.1f}%"


def report(results, market, since):
    lines = [f"## {market} core-hold backfill (trades sold or held since {since})", ""]
    for variant, label in (("a", "(a) as today"), ("b", "(b) trailing exempt")):
        rows = []
        for r in results:
            st = status(r, variant)
            if st in ("WATCH", "SOLD_BEFORE_PROMOTION", "NOT_APPLICABLE", "UNKNOWN"):
                continue
            if st == "CAP_SKIPPED" and not (variant == "b" and r.get("b_applies")):
                continue                                  # a skipped real position keeps its real exit
            actual, rule, still = outcome(r, variant)
            rows.append((r, st, actual, rule, still))
        lines += [f"### {label}: {len(rows)} trades changed by the rule", "",
                  "| ticker | buy | status | promoted (regime) | actual exit | rule exit | actual | rule | diff |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r, st, actual, rule, still in rows:
            cand = r.get(variant) or {}
            rule_exit = (cand.get("exit") if cand.get("slot")
                         else ((r.get("b_path") or {}).get("exit") if variant == "b" else None))
            rule_txt = (f"{rule_exit['date']} {rule_exit['code']}" if rule_exit else "still holding")
            act = r.get("actual_exit")
            diff = None if actual is None or rule is None else rule - actual
            promo = (f"{cand['date']} ({(cand.get('regime') or {}).get('regime') or 'MISSING'})" if cand else "")
            lines.append(f"| {r['ticker']} | {str(r['buy_date'])[:10]} | {st} | {promo} | "
                         f"{act['date'] + ' ' + str(act.get('exit_kind')) if act else 'open'} | {rule_txt} | "
                         f"{_pct(actual)} | {_pct(rule)}{' (open)' if still else ''} | {_pct(diff)} |")
        groups = [("actual", 2, rows), ("rule", 3, rows)]
        if variant == "b":                               # exit_kind 'stop' = the stop-loss loop (trailing/raised stop)
            stops = [row for row in rows if (row[0].get("actual_exit") or {}).get("exit_kind") == "stop"]
            groups += [("actual, exit_kind=stop", 2, stops), ("rule, exit_kind=stop", 3, stops)]
        for name, idx, subset in groups:
            s = _stats([row[idx] for row in subset])
            rate = "MISSING" if s["win_rate"] is None else f"{s['win_rate'] * 100:.0f}%"
            lines.append(f"- {name}: n={s['n']}, win rate {rate}, avg win {_pct(s['avg_win'])}, "
                         f"avg loss {_pct(s['avg_loss'])}, payoff "
                         f"{'MISSING' if s['payoff'] is None else format(s['payoff'], '.2f')}")
        lines.append("")
    unknown = [r["ticker"] for r in results if r.get("error")]
    lines.append(f"- positions: {len(results)}, MISSING bars/dates: {len(unknown)} {unknown[:20]}")
    flagged = [r["ticker"] for r in results if r.get("corporate_action")]
    lines.append(f"- split/share issue inside the 200-session window (MA200 may be off): {flagged}")
    return "\n".join(lines) + "\n"


REGIME_CLOSE = {"KR": ("Asia/Seoul", "15:30"), "US": ("America/New_York", "16:00")}
REGIME_MAX_AGE = timedelta(days=4)


def load_regimes(market, path=None):
    """[(KST timestamp, row)] of the market's logged regime snapshots (logs/regime_history.jsonl)."""
    rows = []
    try:
        with open(path or ROOT / "logs/regime_history.jsonl", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    if str(row.get("market") or "").upper() == market:
                        rows.append((datetime.strptime(str(row["ts"])[:19], "%Y-%m-%d %H:%M:%S"), row))
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        return []
    return sorted(rows, key=lambda item: item[0])


def regime_at(rows, market, session_date):
    """The system regime logged by the promotion close (KST rows), or None (MISSING) when none is recent."""
    from zoneinfo import ZoneInfo
    tz, hhmm = REGIME_CLOSE[market]
    close = datetime.strptime(f"{session_date} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(tz))
    close_kst = close.astimezone(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    found = None
    for ts, row in rows:
        if ts > close_kst:
            break
        found = (ts, row)
    if found is None or close_kst - found[0] > REGIME_MAX_AGE:
        return None
    return {"regime": found[1].get("regime"), "confidence": found[1].get("confidence"), "ts": str(found[0])}


def run(market, *, since, today, fetch=fetch_bars, path=None, pause=0.0, regimes=None):
    results = []
    for position in load_positions(market, since, path):
        if pause:
            time.sleep(pause)                          # a backfill shares the broker rate limit with live loops
        try:
            buy = datetime.strptime(str(position["buy_date"])[:10], "%Y-%m-%d").date()
            bars, corporate = fetch(market, position["ticker"], buy - timedelta(days=HISTORY_PAD_DAYS), today)
            from prism_core import runner_hold as R
            bars = R.completed_bars(bars, market=market, now=datetime.now(timezone.utc))
        except Exception as error:  # noqa: BLE001 - one ticker's outage stays MISSING
            print(f"[core_hold][{market}] {position['ticker']} bars unavailable: {type(error).__name__}: {error}")
            bars, corporate = [], None
        results.append(evaluate(position, market, bars, corporate, today=today))
    allocate(results, "a")
    allocate(results, "b")
    rows = load_regimes(market) if regimes is None else regimes
    for r in results:                                   # the market regime on each promotion (bear-regime question)
        for variant in ("a", "b"):
            if r.get(variant):
                r[variant]["regime"] = regime_at(rows, market, r[variant]["date"])
    return results


def _emit(market, result, before, after):
    from observability.events import emit_event
    from prism_core.trace_ids import trade_trace
    emit_event("core_hold.observed", service=f"prism-{market.lower()}-core-hold", market=market,
               ticker=result["ticker"], decision_id=result.get("decision_id"),
               trace_id=trade_trace(market, decision_id=result.get("decision_id"), ticker=result["ticker"]),
               attributes={"contract": C.VERSION, "record_only": True, "key": result["key"], "from": before,
                           "to": after, "a": _brief(result.get("a")), "b": _brief(result.get("b")),
                           "actual_exit": result.get("actual_exit"), "corporate_action": result.get("corporate_action")})


def _brief(cand):
    if not cand:
        return None
    return {k: cand.get(k) for k in ("date", "slot", "exit", "exit_first_basis", "share", "ret", "regime")} | {
        "view": cand.get("view")}


def record(market, results, *, today, dry_run=False):
    state_path = ROOT / f"runtime/core_hold_{market.lower()}.json"
    journal = ROOT / f"runtime/core_hold_journal_{market.lower()}.jsonl"
    try:
        previous = json.loads(state_path.read_text()).get("positions", {})
    except (OSError, ValueError):
        previous = {}
    positions, lines = {}, []
    for r in results:
        st_a, st_b = status(r, "a"), status(r, "b")
        a_ret = outcome(r, "a")
        b_ret = outcome(r, "b")
        positions[r["key"]] = {"ticker": r["ticker"], "status": st_a, "status_b": st_b,
                               "promoted_on": (r.get("a") or {}).get("date"),
                               "entry_avg": r.get("entry_avg"), "entry_first": r.get("entry_first")}
        lines.append({"date": today.isoformat(), "contract": C.VERSION, "market": market, **{
            k: r.get(k) for k in ("key", "ticker", "open", "entry_session", "entry_avg", "entry_first", "legs",
                                  "actual_exit", "corporate_action", "last_close", "error")},
            "status": st_a, "status_b": st_b, "latest": r.get("latest"), "a": _brief(r.get("a")),
            "b": _brief(r.get("b")), "b_path": r.get("b_path"),
            "return_a": {"actual": a_ret[0], "rule": a_ret[1], "rule_open": a_ret[2]},
            "return_b": {"actual": b_ret[0], "rule": b_ret[1], "rule_open": b_ret[2]}})
        old = previous.get(r["key"], {})
        if (old.get("status"), old.get("status_b")) != (st_a, st_b) and not dry_run:
            try:
                _emit(market, r, [old.get("status"), old.get("status_b")], [st_a, st_b])
            except Exception as error:  # noqa: BLE001 - observation must never fail the run
                print(f"[core_hold][{market}] event failed: {type(error).__name__}")
    if dry_run:
        return positions
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"contract": C.VERSION, "updated": today.isoformat(), "record_only": True,
                                      "positions": positions}, ensure_ascii=False, indent=1, default=str))
    with journal.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    return positions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", required=True, choices=("KR", "US"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backfill-since", help="one-off report over trades sold since this date; no state")
    parser.add_argument("--out", help="backfill report path (markdown)")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except Exception as error:  # noqa: BLE001 - the environment may already carry the settings
        print(f".env not loaded: {type(error).__name__}", file=sys.stderr)
    os.environ.setdefault("PRISM_DISABLE_SIGNAL_PUBLISH", "1")
    if not enabled():
        print(f"Core-hold {args.market} summary: disabled")
        return 0
    from prism_core import runner_hold as R
    today = datetime.now(R._sessions()[args.market][0]).date()
    if args.backfill_since:
        results = run(args.market, since=args.backfill_since, today=today, pause=0.5)
        text = report(results, args.market, args.backfill_since)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    from prism_core.add_plan import is_session
    if not is_session(args.market, today):
        print(f"Core-hold {args.market} summary: no session {today}")
        return 0
    results = run(args.market, since=REGISTERED, today=today)
    positions = record(args.market, results, today=today, dry_run=args.dry_run)
    counts = {}
    for p in positions.values():
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    missing = sum(1 for r in results if r.get("error"))
    print(f"Core-hold {args.market} summary: positions={len(results)} statuses={counts} missing={missing}"
          + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
