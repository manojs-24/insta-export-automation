"""Selector discovery helper: save the accessibility tree of pages you navigate to.

Snapshots list roles and accessible names (what role/label/text selectors match). They
can contain account names and other personal data, so they are written to a gitignored
folder and never uploaded or logged. Review before sharing.
"""

import re
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:40] or "page"


def inspect_pages(
    page: Page,
    out_dir: Path,
    ask: Callable[[str], str] = input,
    notify: Callable[[str], None] = print,
) -> list[Path]:
    """Loop: user navigates in the browser, names the page, a snapshot is saved."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    while True:
        label = ask("Navigate to the page to capture, then type a label (blank to finish): ").strip()
        if not label:
            return saved
        path = out_dir / f"{len(saved) + 1:02d}_{_slug(label)}.yml"
        header = f"# url: {page.url.split('?')[0]}\n"
        path.write_text(header + page.locator("body").aria_snapshot(), encoding="utf-8")
        saved.append(path)
        notify(f"Saved {path}")
