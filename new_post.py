#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRAFTS_DIR = ROOT / "_drafts"
DRAFTS_DIR.mkdir(exist_ok=True)

title = input("Enter post title: ")
if not title:
    exit(0)

# Generate filename
now = datetime.now().astimezone()
datetime_str = now.strftime("%Y-%m-%d %H:%M %z")
slug = title.lower().replace(" ", "-").replace("'", "").replace("`", "")
filename = DRAFTS_DIR / f"{slug}.md"

# Create the file
frontmatter = f"""---
title: "{title}"
date: {datetime_str}
---

"""
with open(filename, "w") as f:
    f.write(frontmatter)

print(f"Created: {filename.relative_to(ROOT)}")
