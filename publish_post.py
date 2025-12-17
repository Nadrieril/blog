#!/usr/bin/env python3
"""Promote a draft post by renaming it and updating its front matter date."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
POSTS_DIR = ROOT / "_posts"
POSTS_DIR.mkdir(exist_ok=True)
DRAFTS_DIR = ROOT / "_drafts"
DRAFTS_DIR.mkdir(exist_ok=True)

def list_drafts() -> list[Path]:
    return sorted(DRAFTS_DIR.glob("*.md"))

def choose_draft(drafts: list[Path]) -> Path | None:
    """Return the selected draft using fzf when available."""
    if not drafts:
        print("No draft posts found.")
        return None

    rel_paths = [str(p.relative_to(ROOT)) for p in drafts]
    fzf = shutil.which("fzf")
    if fzf:
        try:
            proc = subprocess.run(
                [fzf, "--prompt", "Publish draft > ", "--height", "40%", "--reverse"],
                input="\n".join(rel_paths) + "\n",
                text=True,
                capture_output=True,
            )
        except OSError:
            proc = None
        if proc and proc.returncode == 0:
            selection = proc.stdout.strip()
            if selection:
                return drafts[rel_paths.index(selection)]
            return None

    print("Select a draft to publish:")
    for idx, rel in enumerate(rel_paths, start=1):
        print(f"{idx}. {rel}")

    while True:
        try:
            choice = input("Enter number (or blank to cancel): ").strip()
        except EOFError:
            return None
        if choice == "":
            return None
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(drafts):
                return drafts[index]
        print("Invalid selection, try again.")

def _insert_date_field(text: str, date_str: str, path: Path) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RuntimeError(f"File {path} does not start with front matter")

    closing_index = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = idx
            break
    if closing_index is None:
        raise RuntimeError(f"File {path} is missing closing front matter delimiter")

    frontmatter_lines = lines[1:closing_index]
    date_line = f"date: {date_str}"

    for idx, line in enumerate(frontmatter_lines):
        if line.startswith("date:"):
            frontmatter_lines[idx] = date_line
            break
    else:
        title_index = next((i for i, line in enumerate(frontmatter_lines) if line.startswith("title:")), None)
        insert_at = title_index + 1 if title_index is not None else 0
        frontmatter_lines.insert(insert_at, date_line)

    remainder = lines[closing_index + 1 :]
    new_lines = ["---", *frontmatter_lines, "---", *remainder]
    new_text = "\n".join(new_lines)
    if text.endswith("\n"):
        new_text += "\n"
    return new_text


def update_frontmatter_date(path: Path, new_datetime: datetime) -> None:
    text = path.read_text(encoding="utf-8")
    date_str = new_datetime.strftime("%Y-%m-%d %H:%M %z")
    new_text, count = re.subn(
        r"^date:\s*.*$",
        f"date: {date_str}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        new_text = _insert_date_field(text, date_str, path)
    path.write_text(new_text, encoding="utf-8")

def promote_draft(draft_path: Path) -> Path:
    now = datetime.now().astimezone()
    update_frontmatter_date(draft_path, now)

    slug = draft_path.stem
    date_prefix = now.strftime("%Y-%m-%d")
    new_name = f"{date_prefix}-{slug}{draft_path.suffix}"
    new_path = POSTS_DIR / new_name
    draft_path.rename(new_path)
    return new_path

def main() -> int:
    drafts = list_drafts()
    selected = choose_draft(drafts)
    if not selected:
        return 0

    new_path = promote_draft(selected)
    print(f"Published {selected.name} -> {new_path.name}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
