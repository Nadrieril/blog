#!/usr/bin/env python3
import os
import subprocess
from datetime import datetime

title = input("Enter post title: ")
if not title:
    exit(0)

# Generate filename
date_str = datetime.now().strftime("%Y-%m-%d")
datetime_str = datetime.now().strftime("%Y-%m-%d %H:%M")
slug = title.lower().replace(" ", "-").replace("'", "").replace("`", "")
filename = f"_posts/{date_str}-{slug}.md"

# Create the file
frontmatter = f"""---
title: "{title}"
date: {datetime_str}
---

"""
with open(filename, "w") as f:
    f.write(frontmatter)

# Open with nvim
subprocess.run(["nvim", filename])

# Check if file was modified
with open(filename, "r") as f:
    content = f.read()
if content.strip() == frontmatter.strip():
    os.remove(filename)
    print(f"Deleted: {filename} (no changes made)")
else:
    print(f"Created: {filename}")
