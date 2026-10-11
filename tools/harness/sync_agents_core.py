#!/usr/bin/env python3
"""Copy docs/HARNESS_CORE_ko.md into AGENTS.md between the HARNESS_CORE markers.

Codex cannot import files from AGENTS.md, so it keeps a copy; Claude imports the
original with ``@docs/HARNESS_CORE_ko.md``. ``tests/test_harness_layers.py`` fails
when the copy drifts. ``--check`` only reports.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "docs" / "HARNESS_CORE_ko.md"
AGENTS = ROOT / "AGENTS.md"
BEGIN = "<!-- HARNESS_CORE:BEGIN (generated from docs/HARNESS_CORE_ko.md; edit that file) -->"
END = "<!-- HARNESS_CORE:END -->"


def render(agents_text: str, core_text: str) -> str:
    start, end = agents_text.index(BEGIN), agents_text.index(END)
    return agents_text[: start + len(BEGIN)] + "\n" + core_text.strip() + "\n" + agents_text[end:]


def main(argv: list[str]) -> int:
    current = AGENTS.read_text(encoding="utf-8")
    updated = render(current, CORE.read_text(encoding="utf-8"))
    if updated == current:
        print("AGENTS.md harness core is up to date")
        return 0
    if "--check" in argv:
        print("AGENTS.md harness core is stale; run tools/harness/sync_agents_core.py")
        return 1
    AGENTS.write_text(updated, encoding="utf-8")
    print("AGENTS.md harness core updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
