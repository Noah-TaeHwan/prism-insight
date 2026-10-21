"""Base pocket pivot detector (H-1022-basepp, definition v1.1 + 2026-10-22 market constants).

Pure functions: no I/O, no clock, no provider calls. The caller passes completed daily bars
(Open/High/Low/Close/Volume, ascending dates); the last row is the evaluated session ``d``.
Too little history or a malformed bar returns ``status='MISSING'`` with a reason code — never
a pass and never zeros.

Source of the numbers: ``tasks/research-20261022/base-pp`` (PREREG "정의 v1.1" and the
"수치 최적화" entry, research implementation ``pp_detect.py`` / ``pp_detect_v11.py``). The
metric formulas below are the research formulas unchanged, so the same bars give the same
values (``tests/test_pocket_pivot.py`` holds golden values from the research code).

Gates (all must hold for PASS):
  PP1  up day: Close_d > Close_{d-1}                                            NOT_UP_DAY
  PP2  Volume_d > max volume of down days (Close < previous Close) in the prior 10 sessions;
       none -> prior 15; still none -> no signal                                NO_DOWN_DAY /
                                                                                NO_VOLUME_SIGNATURE
  MA   Close_d >= MA50_d (MA includes d)                                        BELOW_MA50
  PP0  sessions since the latest highest close of the prior 252 sessions
       >= base_min                                                              BASE_TOO_SHORT
  PP9  gap >= 2 x ATR20% or day change >= 3 x ATR20%                            GAP_OR_SURGE
  PP5  (max - min Close over the prior 20 sessions) / Close_{d-1}
       < pp5_max x ATR20%                                                       NOT_CONTRACTED_PRICE
  ATR20% = mean True Range over d-20..d-1 / Close_{d-1} (known before d's bar).

Recorded only (never gates): base depth and its bucket, volume dry-up (avg vol 10 / 50),
PP6 drift direction, kind (PP_ONLY / BO_ONLY / BOTH / NONE), signature_strength,
pp_seq_in_base, low vs MA10, diagnostic codes RISING_WEDGE and V_SPIKE, the two pivots and
the structural stop candidate (design v0.1: lowest low of the prior 40 sessions x 0.99; the
50-day line is left to the existing close-confirmed trend exit).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

DEFINITION_VERSION = "pp_base_v1.1_mkt_20261022"
TRIGGER_TYPE = "Pocket Pivot Base"

MIN_HISTORY = 60            # sessions before d; MA50 + 10 (research MIN_HIST)
BASE_HIGH_WINDOW = 252      # PP0 base-high window
PIVOT_BASE_WINDOW = 40      # design v0.1: add/target pivot and structural stop window
STRUCTURAL_STOP_BUFFER = 0.99
PP2_WINDOWS = (10, 15)
RANK_TOP_N = 2


@dataclass(frozen=True)
class PocketPivotParams:
    base_min: int
    pp5_max: float
    optimized: bool


# US: plateau choice of the 81-combo training grid (select_us.json: PP0 40, PP5 3.0, depth and
# dry-up axes "none"). KR uses the same values because no KR combo survived: all 81 had a
# non-positive non-bull expectancy in the 2019-07..2023-12 training window (REPORT_ko.md).
MARKET_PARAMS: Mapping[str, PocketPivotParams] = {
    "US": PocketPivotParams(base_min=40, pp5_max=3.0, optimized=True),
    "KR": PocketPivotParams(base_min=40, pp5_max=3.0, optimized=False),
}
KR_NOT_OPTIMIZED = True

# Selection/BUY use of this trigger (layers L2/L3) does not exist yet, so live use is refused for
# every market. KR stays refused even after L2/L3 until a KR study finds a surviving combination
# (design v0.1 item 4): the direction is shared with US, only KR execution waits for evidence.
LIVE_IMPLEMENTED = False
KR_LIVE_BLOCK_REASON = (
    "KR_NO_EVIDENCE: 2026-10-22 base-pp study, all 81 KR combos had non-bull expectancy <= 0")


def live_allowed(market: str) -> tuple[bool, str]:
    """Whether this trigger may feed selection in ``market``. Always refused today."""
    if str(market).upper() == "KR":
        return False, KR_LIVE_BLOCK_REASON
    if not LIVE_IMPLEMENTED:
        return False, "L2_L3_NOT_IMPLEMENTED"
    return True, ""


def params_for(market: str) -> PocketPivotParams:
    return MARKET_PARAMS[str(market).upper()]


def _slope(y: np.ndarray) -> float:
    x = np.arange(len(y), dtype=float)
    x -= x.mean()
    return float((x * (y - y.mean())).sum() / (x * x).sum())


def depth_bucket(depth) -> str | None:
    if depth is None or not math.isfinite(depth):
        return None
    return "<=33" if depth <= 0.33 else ("33-50" if depth <= 0.50 else ">50")


def _missing(code: str, market: str, **metrics) -> dict:
    return {"status": "MISSING", "reason_codes": [code], "diag_codes": [], "metrics": metrics,
            "version": DEFINITION_VERSION, "market": market}


def _date_text(value) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value)


def _v_spike(O, L, C, n: int) -> tuple[bool, int | None, int | None]:
    """Peak p in d-30..d-1 at +25% over the window low, then a gap down (Open_j < Low_{j-1}) or
    a MA20 touch on some j in p+1..d, then Close > Close_p within j+1..j+3 (capped at d)."""
    ma20 = pd.Series(C).rolling(20).mean().to_numpy()
    start = max(0, n - 30)
    for p in range(start, n):
        if C[p] / C[start:p + 1].min() - 1 < 0.25:
            continue
        for j in range(p + 1, n + 1):
            if O[j] < L[j - 1] or L[j] <= ma20[j]:
                if any(C[r] > C[p] for r in range(j + 1, min(j + 4, n + 1))):
                    return True, p, j
    return False, None, None


def _pp_signature_days(C, V, ma50s, lo: int, hi: int) -> int:
    """Count sessions j in [lo, hi] with PP1 + PP2 + Close >= MA50 (the core signature)."""
    count = 0
    for j in range(max(lo, 16), hi + 1):
        if not (C[j] > C[j - 1]) or not (ma50s[j] <= C[j]):
            continue
        maxdv = None
        for window in PP2_WINDOWS:
            idx = [k for k in range(j - window, j) if C[k] < C[k - 1]]
            if idx:
                maxdv = max(V[k] for k in idx)
                break
        if maxdv is not None and V[j] > maxdv:
            count += 1
    return count


def detect(bars: pd.DataFrame, *, market: str = "US", params: PocketPivotParams | None = None,
           signal_date=None) -> dict:
    """Evaluate the base pocket pivot on the last bar (or on ``signal_date``).

    Returns ``{'status': 'PASS'|'FAIL'|'MISSING', 'reason_codes', 'diag_codes', 'metrics',
    'version', 'market'}``. FAIL lists every failed gate; MISSING lists why it is unknown.
    """
    market = str(market).upper()
    params = params or params_for(market)
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return _missing("NO_HISTORY", market)
    if not {"Open", "High", "Low", "Close", "Volume"}.issubset(bars.columns):
        return _missing("BAD_COLUMNS", market)
    b = bars.sort_index()
    if signal_date is not None:
        target = pd.Timestamp(signal_date).normalize()
        index = pd.to_datetime(b.index)
        if getattr(index, "tz", None) is not None:
            index = index.tz_localize(None)
        index = index.normalize()
        b = b.loc[index <= target]
        if b.empty or index[len(b) - 1] != target:
            return _missing("NO_BAR", market)
    b = b[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    b = b.dropna(subset=["Close"])
    n = len(b) - 1
    if n < MIN_HISTORY:
        return _missing("SHORT_HISTORY", market, history=max(n, 0))
    O, H, L, C, V = (b[c].to_numpy(dtype=float) for c in ("Open", "High", "Low", "Close", "Volume"))
    if not (V[n] > 0) or not np.isfinite(O[n]) or not np.isfinite(H[n]) or not np.isfinite(L[n]):
        return _missing("BAD_BAR", market)
    pc = np.r_[np.nan, C[:-1]]
    tr = np.fmax(np.fmax(H - L, np.abs(H - pc)), np.abs(L - pc))
    with np.errstate(all="ignore"):
        atrp = float(np.nanmean(tr[n - 20:n]) / C[n - 1])
    if not math.isfinite(atrp) or atrp <= 0 or not (C[n - 1] > 0):
        return _missing("BAD_HISTORY", market)
    ma10 = C[n - 9:n + 1].mean()
    ma50 = C[n - 49:n + 1].mean()
    m: dict = {"atr20pct": atrp, "ma10": ma10, "ma50": ma50, "close": C[n],
               "signal_date": _date_text(b.index[n])}
    rc: list[str] = []
    dc: list[str] = []

    # PP1 / PP2
    up = bool(C[n] > C[n - 1])
    if not up:
        rc.append("NOT_UP_DAY")
    down = C[1:] < C[:-1]                       # down[j-1] -> bar j is a down day
    maxdv = None
    m["pp2_window"] = None
    for window in PP2_WINDOWS:
        idx = [j for j in range(n - window, n) if down[j - 1]]
        if idx:
            maxdv = max(V[j] for j in idx)
            m["pp2_window"] = window
            break
    if maxdv is None:
        rc.append("NO_DOWN_DAY")
        m["signature_strength"] = None
    else:
        m["signature_strength"] = V[n] / maxdv
        if not V[n] > maxdv:
            rc.append("NO_VOLUME_SIGNATURE")

    # PP0 base length (latest max close over d-252..d-1) and depth
    off = n - BASE_HIGH_WINDOW if n >= BASE_HIGH_WINDOW else 0
    w = C[off:n]
    hi_pos = off + int(np.flatnonzero(w == w.max())[-1])
    base_len = n - hi_pos
    base_hi = C[hi_pos]
    m["base_length"] = base_len
    m["base_window"] = n - off
    m["base_high_date"] = _date_text(b.index[hi_pos])
    m["pivot_252"] = base_hi
    if base_len < params.base_min:
        rc.append("BASE_TOO_SHORT")
    low_in_base = np.nanmin(L[hi_pos:n]) if n > hi_pos else L[n - 1]
    depth = 1 - low_in_base / base_hi
    m["base_depth"] = depth
    m["depth_bucket"] = depth_bucket(depth)

    # Close vs MA50, low vs MA10
    m["close_vs_ma50"] = C[n] / ma50 - 1
    m["low_vs_ma10"] = L[n] / ma10 - 1
    if C[n] < ma50:
        rc.append("BELOW_MA50")

    # PP5 contraction
    rng20 = (C[n - 20:n].max() - C[n - 20:n].min()) / C[n - 1]
    ratio = rng20 / atrp
    m["pp5_range20"] = rng20
    m["pp5_ratio_atr"] = ratio
    if not ratio < params.pp5_max:
        rc.append("NOT_CONTRACTED_PRICE")

    # PP6 (recorded): volume dry-up and drift direction into the PP
    v10, v50 = V[n - 10:n].mean(), V[n - 50:n].mean()
    m["vol_dryup_10_50"] = v10 / v50 if v50 > 0 else None
    drift = C[n - 1] / C[n - 11] - 1
    m["pp6_drift10_atr"] = drift / atrp
    m["pp6_rising_into_pp"] = bool(drift > atrp)

    # PP9 gap / surge
    gap, chg = O[n] / C[n - 1] - 1, C[n] / C[n - 1] - 1
    m["gap_atr"] = gap / atrp
    m["chg_atr"] = chg / atrp
    if gap >= 2 * atrp or chg >= 3 * atrp:
        rc.append("GAP_OR_SURGE")

    # Diagnostics
    cs, vv = C[n - 15:n], V[n - 15:n]
    if _slope(cs) > 0 and _slope(vv) < 0 and C[n - 1] / C[n - 15] - 1 >= 2 * atrp:
        dc.append("RISING_WEDGE")
    spike, peak, brk = _v_spike(O, L, C, n)
    if spike:
        dc.append("V_SPIKE")
        m["v_spike_peak_date"] = _date_text(b.index[peak])
        m["v_spike_break_date"] = _date_text(b.index[brk])

    # Kind: BO = close above the prior 50-session highest close on >= 1.5x 50-day average volume
    hi50 = C[n - 50:n].max()
    bo = bool(C[n] > hi50 and V[n] >= 1.5 * v50)
    pp = bool(up and maxdv is not None and V[n] > maxdv)
    m["kind"] = "BOTH" if (pp and bo) else ("PP_ONLY" if pp else ("BO_ONLY" if bo else "NONE"))
    m["quiet_pp"] = bool(V[n] < v50)

    # Order of today's signature inside the current base (1 = first since the base high)
    ma50s = pd.Series(C).rolling(50).mean().to_numpy()
    earlier = _pp_signature_days(C, V, ma50s, hi_pos + 1, n - 1)
    m["pp_seq_in_base"] = earlier + 1 if (pp and C[n] >= ma50) else None

    # Pivots and the structural stop candidate (design v0.1)
    pivot_base = C[n - PIVOT_BASE_WINDOW:n].max()
    stop = float(np.nanmin(L[n - PIVOT_BASE_WINDOW:n])) * STRUCTURAL_STOP_BUFFER
    m["pivot_base"] = pivot_base
    m["pivot_base_vs_close_pct"] = (pivot_base / C[n] - 1) * 100
    m["structural_stop"] = stop
    m["structural_stop_pct"] = (stop / C[n] - 1) * 100

    m = {k: _clean(v) for k, v in m.items()}
    return {"status": "PASS" if not rc else "FAIL", "reason_codes": rc, "diag_codes": dc,
            "metrics": m, "version": DEFINITION_VERSION, "market": market}


# --- ② continuation pocket pivot (pure helper only; nothing calls it yet) -------------------

CONT_MIN_HISTORY = 50       # completed sessions before the evaluated day (MA20 valid over V_SPIKE window)
CONT_PP2_WINDOW = 10
CONT_WEDGE_WINDOW = 20


def continuation_pp(bars: pd.DataFrame, *, today: Mapping | None = None) -> dict:
    """② continuation pocket pivot on completed daily bars, optionally with today's partial bar.

    ``bars``: completed sessions (Open/High/Low/Close/Volume, ascending). Without ``today`` the
    last row is the evaluated session. With ``today`` (``Open``, ``High``, ``Low``, ``Close`` or
    ``price``, cumulative ``Volume``) the partial bar is evaluated against all ``bars`` and MA10
    uses the last 9 completed closes plus today's price.

    Gates: NOT_UP_DAY (price <= previous close), NOT_AT_MA10 (low > MA10), BELOW_MA10
    (price <= MA10), NO_DOWN_DAY / NO_VOLUME_SIGNATURE (volume vs the largest down-day volume of
    the prior 10 completed sessions), GAP_OR_SURGE (gap >= 2 x ATR20% or change >= 3 x ATR20%).
    Exclusions (also in ``reason_codes``): RISING_WEDGE (prior 20 sessions: close slope > 0,
    volume slope < 0, rise >= 2 x ATR20%) and V_SPIKE (prior 30 sessions +25%, then a gap down
    or MA20 touch, then a close above that peak within 3 sessions).
    """
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return _missing("NO_HISTORY", "")
    if not {"Open", "High", "Low", "Close", "Volume"}.issubset(bars.columns):
        return _missing("BAD_COLUMNS", "")
    b = bars.sort_index()[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    b = b.dropna(subset=["Close"])
    O, H, L, C, V = (b[c].to_numpy(dtype=float) for c in ("Open", "High", "Low", "Close", "Volume"))
    partial = today is not None
    if partial:
        price = today.get("Close", today.get("price"))
        values = [_number(today.get(k)) for k in ("Open", "High", "Low")] + [_number(price),
                                                                               _number(today.get("Volume"))]
        if any(v is None for v in values):
            return _missing("BAD_BAR", "")
        O, H, L, C, V = (np.r_[arr, val] for arr, val in zip((O, H, L, C, V), values))
    n = len(C) - 1
    if n < CONT_MIN_HISTORY:
        return _missing("SHORT_HISTORY", "", history=max(n, 0))
    if not (V[n] > 0) or not all(np.isfinite(x[n]) for x in (O, H, L)):
        return _missing("BAD_BAR", "")
    pc = np.r_[np.nan, C[:-1]]
    tr = np.fmax(np.fmax(H - L, np.abs(H - pc)), np.abs(L - pc))
    with np.errstate(all="ignore"):
        atrp = float(np.nanmean(tr[n - 20:n]) / C[n - 1])
    if not math.isfinite(atrp) or atrp <= 0:
        return _missing("BAD_HISTORY", "")
    ma10 = C[n - 9:n + 1].mean()
    m: dict = {"partial_bar": partial, "atr20pct": atrp, "ma10": ma10, "price": C[n],
               "low_vs_ma10": L[n] / ma10 - 1, "close_vs_ma10": C[n] / ma10 - 1}
    rc: list[str] = []
    if not C[n] > C[n - 1]:
        rc.append("NOT_UP_DAY")
    if L[n] > ma10:
        rc.append("NOT_AT_MA10")
    if not C[n] > ma10:
        rc.append("BELOW_MA10")
    down = [j for j in range(n - CONT_PP2_WINDOW, n) if C[j] < C[j - 1]]
    if not down:
        rc.append("NO_DOWN_DAY")
        m["signature_strength"] = None
    else:
        maxdv = max(V[j] for j in down)
        m["signature_strength"] = V[n] / maxdv
        if not V[n] > maxdv:
            rc.append("NO_VOLUME_SIGNATURE")
    gap, chg = O[n] / C[n - 1] - 1, C[n] / C[n - 1] - 1
    m["gap_atr"], m["chg_atr"] = gap / atrp, chg / atrp
    if gap >= 2 * atrp or chg >= 3 * atrp:
        rc.append("GAP_OR_SURGE")
    w = CONT_WEDGE_WINDOW
    if _slope(C[n - w:n]) > 0 and _slope(V[n - w:n]) < 0 and C[n - 1] / C[n - w] - 1 >= 2 * atrp:
        rc.append("RISING_WEDGE")
    spike, peak, brk = _v_spike(O, L, C, n)
    if spike:
        rc.append("V_SPIKE")
        m["v_spike_peak_pos"], m["v_spike_break_pos"] = peak - n, brk - n
    m = {k: _clean(v) for k, v in m.items()}
    return {"status": "PASS" if not rc else "FAIL", "reason_codes": rc, "diag_codes": [],
            "metrics": m, "version": DEFINITION_VERSION, "kind": "CONTINUATION"}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean(value):
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def rank_candidates(rows: Iterable[Mapping], *, top_n: int = RANK_TOP_N) -> list[dict]:
    """Daily ranking for one market: 0.5 x contraction percentile (tighter = higher) +
    0.5 x RS percentile / 100 (unknown RS counts as 0, as in the research).

    Each row needs ``ticker`` and ``pp5_ratio_atr``; ``rs`` is a 0-100 percentile or None.
    Returns new dicts sorted by score with ``rank_score``, ``rank`` and ``top`` added.
    """
    items = [dict(row) for row in rows]
    if not items:
        return []
    ratios = pd.Series([float(r["pp5_ratio_atr"]) for r in items])
    pct = ratios.rank(ascending=False, pct=True).to_numpy()
    for item, p in zip(items, pct):
        rs = item.get("rs")
        rs_value = float(rs) if rs is not None and math.isfinite(float(rs)) else 0.0
        item["contraction_pct"] = float(p)
        item["rank_score"] = 0.5 * float(p) + 0.5 * rs_value / 100.0
    ordered = sorted(enumerate(items), key=lambda pair: (-pair[1]["rank_score"], pair[0]))
    result = []
    for rank, (_, item) in enumerate(ordered, start=1):
        item["rank"] = rank
        item["top"] = rank <= top_n
        result.append(item)
    return result
