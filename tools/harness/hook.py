#!/usr/bin/env python3
"""Shared Claude Code / Codex hook that brings the PRISM harness into the conversation.

Development-time only: it never blocks, never touches trading code, and exits 0 on any error.

    hook.py prompt   UserPromptSubmit: trading/BTC/review/analysis/deploy words -> harness reminder
    hook.py edit     PreToolUse: editing a trading/BTC file -> harness checklist
    hook.py reset    SessionStart(compact): forget what was announced, so the full reminder returns

Both hosts accept ``{"hookSpecificOutput": {"hookEventName", "additionalContext"}}``.
Claude passes ``tool_input.file_path``; Codex passes the raw apply_patch text somewhere in
``tool_input`` (string or list), so every string in it is scanned for patch headers.
The full reminder is sent once per session and domain; later prompts get one line.
The host commands end in ``|| true`` so a missing script never blocks a session.

Maintenance: rule content lives in the source docs, not here. Adjust KEYWORDS when the
reminder fires too often or too rarely, and the *_PATHS lists when trading files move
(``tests/test_harness_layers.py`` checks that every pattern still matches a file).
"""
from __future__ import annotations

import fnmatch
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

STOCK_PATHS = (
    "trigger_batch.py",
    "prism-us/us_trigger_batch.py",
    "cores/agents/trading_agents.py",
    "prism-us/cores/agents/trading_agents.py",
    "stock_tracking_agent.py",
    "stock_tracking_enhanced_agent.py",
    "prism-us/us_stock_tracking_agent.py",
    "prism-us/us_pending_order_batch.py",
    "reentry_cooldown.py",
    "cores/buy_gate.py",
    "cores/market_pulse.py",
    "cores/regime_policy.py",
    "cores/oneil_fallback.py",
    "tools/trend_exit_seller.py",
    "tools/hardstop_seller.py",
    "tools/loop_b_trend_exit.py",
    "tools/run_reentry_v3_entry.py",
    "tools/run_micro_split_add.py",
    "tools/run_b3_ae_shadow.py",
    "trading/**",
    "prism-us/trading/**",
    "prism_core/add_plan*.py",
    "prism_core/micro_split*.py",
    "prism_core/reentry*.py",
    "prism_core/stopout_reentry.py",
    "prism_core/pivot_reentry.py",
    "prism_core/runner_hold*.py",
    "prism_core/sell_regime_context.py",
    "prism_core/entry_score_policy.py",
    "prism_core/order_budget.py",
    "prism_core/slot_weight.py",
    "prism_core/sector_cap.py",
    "prism_core/sector_trading_criteria.py",
    "prism_core/trigger_quality.py",
    "prism_core/screening_quality.py",
    "prism_core/negative_equity_f2.py",
    "prism_core/oneil_*.py",
    "prism_core/b3_ae_*.py",
    "prism_core/trading_scenario_contract.py",
)
PROMPT_PATHS = (
    "cores/agents/trading_agents.py",
    "prism-us/cores/agents/trading_agents.py",
    "prism_core/add_plan_prompts.py",
)
BTC_PATHS = (
    "prism-btc/**",
    "tools/*btc*",
)

KEYWORDS = {
    "btc": ("btc", "비트코인", "bybit", "바이비트"),
    "deploy": ("배포", "머지", "merge", "deploy", "롤백", "rollback", "장애", "hotfix", "핫픽스"),
    "review": (
        "복기", "왜 샀", "왜 팔았", "왜 안 샀", "왜 안 팔", "왜 매수", "왜 매도", "왜 손절", "왜 보유",
        "trace_id", "추적번호", "추적 번호", "clickstack", "클릭스택", "clickhouse", "매매 기록", "매매 내역",
    ),
    "analysis": ("매수품질", "진입품질", "리플레이", "replay", "재생", "승격", "evidence packet", "패킷"),
    "stock": (
        "매수", "매도", "손절", "익절", "재진입", "증액", "비중", "스크리닝", "트리거", "국면",
        "진입", "청산", "초분할", "하드스탑", "트레일링", "trailing", "손익비", "목표가",
        "매매 프롬프트", "매매 로직", "매매 규칙", "screening", "stop-loss", "sizing",
    ),
}
PREFIX_WORDS = {"btc"}  # BTCUSDT, btc_signal_log

NORTH_STAR = (
    "북극성: 크게 가는 소수 종목 포착·비중 확대, 손실·낙폭, 손절 비용, 스크리닝 정확도로 판단한다."
)
FULL = {
    "stock": [
        "[PRISM 하네스] KR·US 매매 변경 가능성이 있는 요청입니다.",
        NORTH_STAR,
        "- 코드 수정 전 `prism-trading-change-review` 스킬 절차(원문 docs/TRADING_CHANGE_REVIEW_HARNESS.md)와"
        " docs/RESEARCH_LESSONS_ko.md 중복 여부를 확인한다.",
        "- 정의·대상 종목·시간축·성공 기준처럼 방향이 갈리는 것은 사용자에게 먼저 묻는다. '비교'는 지금 일회성으로.",
        "- 헌장의 '넘지 않는 선'(docs/HARNESS_CORE_ko.md): 자동 SHADOW/LIVE 금지, MISSING은 모름,"
        " 한 계층만 변경, LLM 승인 규칙은 LLM 재생으로. 매매 규칙은 KR·US 같은 방향으로 바꾼다.",
    ],
    "prompt": [
        "[PRISM 하네스] 매매 프롬프트 파일입니다. 하네스의 'Prompt framing and logical-consistency review' 1~11번:",
        "- 조립 순서대로 펼쳐 조건→결과로 적고, 코드 상수·실행 시각과 대조한다.",
        "- 같은 모순 유형을 BUY·SELL·재진입 × KR·US × ko·en 전부에서 grep한다.",
        "- 방향이 갈리는 모순은 사용자와 정하고, 프롬프트 해시 테스트는 날짜·이유 주석과 함께 갱신한다.",
    ],
    "btc": [
        "[PRISM 하네스] BTC 작업입니다.",
        "- `prism-btc-change-review` 스킬 절차: docs/BTC_ROADMAP_ko.md에서 마일스톤 ID·선행 조건을 먼저 확인한다.",
        "- 실행·보호 결함이 파라미터 최적화보다 먼저다. 안전 수정과 전략 실험은 분리한다. 실자금은 별도 승인.",
        "- 수익 주장은 docs/BTC_PROOF_GATES_20261010_ko.md와 profitability_gate로만. 테스트 통과·백테스트 수익은 입증이 아니다.",
        "- 로드맵 갱신 시 구현·테스트·CI·배포·관측·수익 입증을 구분한다.",
    ],
    "deploy": [
        "[PRISM 하네스] 머지·배포·장애 수리 요청입니다.",
        "- docs/SERVER_GIT_OPERATIONS_ko.md의 변경 범위별 검증 게이트를 먼저 적용한다: 깨끗한 대상, 검증된 커밋, ff-only, 파괴적 reset/stash/clean 금지.",
        "- 머지 전 PR 헤드·의존 순서·관련 테스트와 모든 상태 검사 SUCCESS(gh pr view N --json statusCheckRollup, license 잡 포함)를 확인한다.",
        "- 배포 전 테스트, 배포 후 스모크, 첫 정기 실행 완료 여부를 따로 보고한다. 테스트 통과는 운영 검증이 아니다.",
    ],
    "analysis": [
        "[PRISM 하네스] 데이터 분석·재생 요청입니다.",
        "- `prism-entry-quality-analysis` 스킬: 결정론적 Evidence Packet으로만 분석하고 임의 SQL로 대신하지 않는다.",
        "- MISSING은 모름. 운영에서 LLM이 승인하는 규칙은 LLM 재생으로 판정. 가상 표본은 (시장·종목·규칙·날짜) 중복 제거.",
        "- 승격 순서는 사전등록 → 재생 → SHADOW → 제한 LIVE. SHADOW·LIVE 자동 승격 금지, LIVE는 명시 승인.",
    ],
    "review": [
        "[PRISM 하네스] 매매 복기·판단 이유 확인 요청입니다.",
        "- `prism-trade-review` 스킬: 먼저 `python tools/clickstack_query.py ticker <종목>` → `trace <trace_id>`로"
        " ClickStack 원장을 조회한다. 서버 SQLite·로그 grep은 원장에 없는 부분을 메울 때만 쓴다.",
        "- 원장에 없는 사건은 MISSING으로 둔다(매매 연결은 2026-10-08 이후 판단부터, 보관 180일). 기억·추측으로 메우지 않는다.",
        "- 한 건의 사실 확인용이다. 여러 건을 묶은 성과 판정·규칙 변경 근거는 Evidence Packet과 매매 변경 절차로 간다.",
    ],
}
SHORT = {
    "stock": "[PRISM 하네스] (재알림) 매매 변경 → prism-trading-change-review 절차·헌장 '넘지 않는 선' 적용.",
    "prompt": "[PRISM 하네스] (재알림) 프롬프트 변경 → 형제 프롬프트·코드 대조, 방향 갈리는 모순은 사용자와.",
    "btc": "[PRISM 하네스] (재알림) BTC → 로드맵 마일스톤 ID 확인, 안전 수정과 전략 실험 분리, 수익은 입증 계약으로만.",
    "deploy": "[PRISM 하네스] (재알림) 머지·배포 → SERVER_GIT_OPERATIONS 게이트, 모든 상태 검사 SUCCESS, 배포·첫 정기 실행 따로 보고.",
    "review": "[PRISM 하네스] (재알림) 복기 → tools/clickstack_query.py ticker·trace로 원장부터, 없으면 MISSING.",
    "analysis": "[PRISM 하네스] (재알림) 분석 → Evidence Packet·MISSING 보존·LLM 재생·중복 제거.",
}
ORDER = ("btc", "stock", "prompt", "review", "analysis", "deploy")
PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)


def match_path(rel: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(rel, p) for p in patterns)


def path_domains(rel: str) -> set[str]:
    found = set()
    if match_path(rel, BTC_PATHS):
        found.add("btc")
    if match_path(rel, STOCK_PATHS):
        found.add("stock")
    if match_path(rel, PROMPT_PATHS):
        found.add("prompt")
    return found


def _has_word(low: str, word: str) -> bool:
    if word.isascii():  # whole words, so "sizing" does not hit "resizing"
        tail = "" if word in PREFIX_WORDS else "(?![a-z])"
        return re.search(rf"(?<![a-z]){re.escape(word)}{tail}", low) is not None
    return word in low


def prompt_domains(text: str) -> set[str]:
    low = text.lower()
    found = {d for d, words in KEYWORDS.items() if any(_has_word(low, w) for w in words)}
    if "btc" in found:
        found.discard("stock")  # BTC wording (진입·청산·손절) should not also trigger the stock reminder
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def edited_paths(payload: dict) -> list[str]:
    tool_input = payload.get("tool_input")
    raw = []
    if isinstance(tool_input, dict):
        raw += [tool_input.get(k) for k in ("file_path", "path", "notebook_path")]
    for text in _strings(tool_input):
        if "*** Begin Patch" in text:
            raw += [a or b for a, b in PATCH_FILE.findall(text)]
    base = Path(payload.get("cwd") or ROOT)
    out = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item.strip())
        path = path if path.is_absolute() else base / path
        try:
            out.append(path.resolve().relative_to(ROOT).as_posix())
        except ValueError:
            continue  # outside this repository
    return out


def _state_file(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:120]
    return Path(tempfile.gettempdir()) / "prism-harness-hook" / f"{safe}.json"


def first_time(session_id: str | None, domains: set[str]) -> set[str]:
    """Return the domains not yet announced in this session and record them."""
    if not session_id:
        return set(domains)
    path = _state_file(session_id)
    try:
        seen = set(json.loads(path.read_text()))
    except (OSError, ValueError):
        seen = set()
    new = domains - seen
    if new:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sorted(seen | new)))
        except OSError:
            pass
    return new


def build_message(domains: set[str], new: set[str]) -> str:
    lines = []
    for domain in ORDER:
        if domain in domains:
            lines += FULL[domain] if domain in new else [SHORT[domain]]
    return "\n".join(lines)


def run(mode: str, payload: dict) -> dict | None:
    if mode == "reset":  # compaction drops earlier reminders from context
        if payload.get("session_id"):
            _state_file(payload["session_id"]).unlink(missing_ok=True)
        return None
    if mode == "prompt":
        domains = prompt_domains(str(payload.get("prompt") or ""))
        event = "UserPromptSubmit"
    elif mode == "edit":
        domains = set()
        for rel in edited_paths(payload):
            domains |= path_domains(rel)
        event = "PreToolUse"
    else:
        return None
    if not domains:
        return None
    new = first_time(payload.get("session_id"), domains)
    if mode == "edit" and not new:
        return None  # one checklist per domain per session is enough while editing
    return {"hookSpecificOutput": {"hookEventName": event,
                                   "additionalContext": build_message(domains, new)}}


def main(argv: list[str]) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = run(argv[1] if len(argv) > 1 else "", payload)
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:  # a reminder hook must never break the host session
        print(f"prism harness hook skipped: {exc!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
