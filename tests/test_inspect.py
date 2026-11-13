from pathlib import Path

import pytest

from web_automation.browser.inspect import inspect_pages
from web_automation.engine import ExecutionContext

pytestmark = pytest.mark.browser


def test_inspect_saves_accessibility_snapshots(ctx: ExecutionContext, tmp_path: Path) -> None:
    answers = iter(["Home page!", ""])
    saved = inspect_pages(ctx.page, tmp_path / "inspect", ask=lambda _: next(answers), notify=lambda _: None)
    assert [p.name for p in saved] == ["01_Home_page.yml"]
    text = saved[0].read_text(encoding="utf-8")
    assert "# url: http://127.0.0.1" in text and "button" in text and "Go" in text
