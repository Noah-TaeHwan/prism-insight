from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_demo_import_resolves_prism_us_news_prefetch_in_clean_process(tmp_path):
    """깨끗한 프로세스에서도 prism-us 로컬 뉴스 prefetch를 로드해야 한다."""

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    env["FIRECRAWL_API_KEY"] = ""
    probe = """
from pathlib import Path
import cores
import demo
module_path = Path(demo.us_analysis_module.fetch_recent_news.__code__.co_filename).resolve()
expected = (Path.cwd() / 'prism-us' / 'cores' / 'us_news_prefetch.py').resolve()
assert module_path == expected, (module_path, expected)
print('clean_demo_import=PASS')
"""

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean_demo_import=PASS"
