@AGENTS.md

# Claude Code notes

`AGENTS.md` above is the shared guide (harness charter, project map, rules) for Claude and Codex.
Edit shared rules there, and the charter in `docs/HARNESS_CORE_ko.md` (then run
`python tools/harness/sync_agents_core.py`). Keep only Claude-specific notes in this file.

- Skills: `.claude/skills/` links to `skills/` — `prism-trading-change-review`, `prism-btc-change-review`,
  `prism-entry-quality-analysis`. Invoke the matching skill before the work it covers, not after.
- Hooks (`.claude/settings.json` → `tools/harness/hook.py`): trading/BTC/analysis/deploy prompts and
  trading-file edits add a `[PRISM 하네스]` reminder. Treat it as the checklist for that turn. After
  `/compact` the full reminder is shown again on the next matching prompt.
- Subagents: brief them with the relevant charter lines (north star, no auto SHADOW/LIVE, `MISSING`
  stays unknown), branch from `origin/main`, and check their diff with `git diff origin/main --stat`
  before any PR.
- `tasks/lessons.md` and `tasks/STATUS_*.md` are local-only (gitignored) handoff notes; read them when
  present, but put durable rules in tracked docs (`docs/RESEARCH_LESSONS_ko.md`, this harness).
- To check these instruction files for stale or conflicting content, run `/doctor prompt-audit`.
