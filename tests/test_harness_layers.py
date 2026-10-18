"""Keep the Claude/Codex harness layers pointing at the same rules (2026-10-11).

The charter, skills, path rules and hooks are thin pointers to the source documents;
these checks fail when a copy drifts or a pointer breaks.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "prism-trading-change-review", "prism-btc-change-review", "prism-entry-quality-analysis", "prism-trade-review",
)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook = _load("tools/harness/hook.py", "harness_hook")
sync = _load("tools/harness/sync_agents_core.py", "harness_sync")


def test_agents_md_carries_current_charter():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    core = (ROOT / "docs/HARNESS_CORE_ko.md").read_text(encoding="utf-8")
    assert sync.render(agents, core) == agents, "run tools/harness/sync_agents_core.py"


def test_claude_md_imports_shared_guide():
    # With both files present Claude reads only CLAUDE.md, so AGENTS.md must be imported;
    # AGENTS.md already carries the charter, so importing it again would load it twice.
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith("@AGENTS.md\n")
    assert "@docs/HARNESS_CORE_ko.md" not in claude


def test_charter_links_resolve():
    core = (ROOT / "docs/HARNESS_CORE_ko.md").read_text(encoding="utf-8")
    for ref in set(re.findall(r"`((?:docs|tests|tools)/[^`\s]+)`", core)):
        assert (ROOT / ref).exists(), ref
    for name in re.findall(r"`(prism-[a-z-]+)`", core):
        assert (ROOT / "skills" / name / "SKILL.md").exists(), name


@pytest.mark.parametrize("name", SKILLS)
def test_skills_visible_to_both_hosts(name):
    source = ROOT / "skills" / name / "SKILL.md"
    head = source.read_text(encoding="utf-8").split("---")[1]
    assert f"name: {name}" in head and "description:" in head
    for host in (".claude/skills", ".agents/skills"):
        link = ROOT / host / name
        assert link.is_symlink() and (link / "SKILL.md").resolve() == source.resolve()


@pytest.mark.parametrize("name", SKILLS)
def test_skill_doc_references_resolve(name):
    text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    for ref in set(re.findall(r"`((?:docs|tools|tests|prism_core|cores|prism-btc|prism-us)/[^`\s*]+)`", text)):
        assert (ROOT / ref).exists(), ref


def test_hook_patterns_match_existing_files():
    for pattern in hook.STOCK_PATHS + hook.PROMPT_PATHS + hook.BTC_PATHS:
        assert list(ROOT.glob(pattern)), f"pattern matches nothing: {pattern}"


def test_hook_configs_call_shared_script():
    claude = json.loads((ROOT / ".claude/settings.json").read_text())["hooks"]
    codex = json.loads((ROOT / ".codex/hooks.json").read_text())["hooks"]
    for cfg in (claude, codex):
        commands = [h["command"] for e in ("UserPromptSubmit", "PreToolUse") for g in cfg[e] for h in g["hooks"]]
        assert any("hook.py\" prompt" in c for c in commands)
        assert any("hook.py\" edit" in c for c in commands)
        # exit 2 blocks the session in both hosts; a missing script must not
        assert all("|| true" in c for c in commands)
    assert "apply_patch" in codex["PreToolUse"][0]["matcher"]
    reset = claude["SessionStart"][0]
    assert reset["matcher"] == "compact" and "hook.py\" reset || true" in reset["hooks"][0]["command"]


@pytest.mark.parametrize("text, expected", [
    ("손절 폭을 넓혀볼까?", {"stock"}),
    ("BTC 진입 손절 규칙 바꿔줘", {"btc"}),
    ("매수품질 데이터 분석해줘", {"stock", "analysis"}),
    ("please deliver the weekly report", set()),
    ("README 오타 고쳐줘", set()),
    ("BTCUSDT 펀딩 확인", {"btc"}),
    ("매매 로직 바꿔줘", {"stock"}),
    ("live 대시보드 색 바꿔줘", set()),
    ("PR 머지하고 배포해줘", {"deploy"}),
    ("LLY 왜 샀는지 복기해줘", {"review"}),
    ("이 trace_id 경과 보여줘", {"review"}),
])
def test_prompt_domains(text, expected):
    assert hook.prompt_domains(text) == expected


def test_edit_paths_from_claude_and_codex_payloads():
    claude = {"tool_input": {"file_path": str(ROOT / "prism_core/micro_split_live.py")}}
    patch = ("*** Begin Patch\n*** Update File: cores/agents/trading_agents.py\n@@\n-a\n+b\n"
             "*** Add File: prism-btc/live/x.py\n+y\n*** End Patch")
    codex = {"cwd": str(ROOT), "tool_input": {"command": patch}}
    assert hook.edited_paths(claude) == ["prism_core/micro_split_live.py"]
    assert hook.edited_paths(codex) == ["cores/agents/trading_agents.py", "prism-btc/live/x.py"]
    assert hook.path_domains("cores/agents/trading_agents.py") == {"stock", "prompt"}
    assert hook.path_domains("prism-btc/live/x.py") == {"btc"}
    assert hook.path_domains("README.md") == set()
    # Codex 0.157 documents apply_patch as {"command": ["apply_patch", "<patch>"]}
    listed = {"cwd": str(ROOT), "tool_input": {"command": ["apply_patch", patch]}}
    assert hook.edited_paths(listed) == ["cores/agents/trading_agents.py", "prism-btc/live/x.py"]
    assert hook.edited_paths({"cwd": str(ROOT), "tool_input": patch}) == hook.edited_paths(listed)


def test_edit_reminder_once_per_session(tmp_path, monkeypatch):
    monkeypatch.setattr(hook.tempfile, "gettempdir", lambda: str(tmp_path))
    payload = {"session_id": "s1", "tool_input": {"file_path": str(ROOT / "trigger_batch.py")}}
    first = hook.run("edit", payload)
    assert first["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "prism-trading-change-review" in first["hookSpecificOutput"]["additionalContext"]
    assert hook.run("edit", payload) is None
    again = hook.run("prompt", {"session_id": "s1", "prompt": "손절 바꿔줘"})
    assert "(재알림)" in again["hookSpecificOutput"]["additionalContext"]


def test_hook_never_fails_on_bad_input(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: "not json"})())
    assert hook.main(["hook.py", "prompt"]) == 0
    assert capsys.readouterr().out == ""


def test_reset_after_compaction_restores_full_reminder(tmp_path, monkeypatch):
    monkeypatch.setattr(hook.tempfile, "gettempdir", lambda: str(tmp_path))
    prompt = {"session_id": "s2", "prompt": "손절 바꿔줘"}
    hook.run("prompt", prompt)
    assert "(재알림)" in hook.run("prompt", prompt)["hookSpecificOutput"]["additionalContext"]
    assert hook.run("reset", {"session_id": "s2", "source": "compact"}) is None
    assert "(재알림)" not in hook.run("prompt", prompt)["hookSpecificOutput"]["additionalContext"]
