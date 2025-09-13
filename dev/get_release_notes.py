#!/usr/bin/env python

# /// script
# dependencies = ["tomli"]
# ///


import re
from itertools import dropwhile, takewhile
from pathlib import Path

import tomli


def _validate_heading_format(heading: str, version: str):
    pattern = rf"^## v{re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}$"
    if not re.match(pattern, heading.strip()):
        raise ValueError(
            "Invalid heading format in CHANGELOG.md\n"
            f"Actual: {heading.strip()}\n"
            f"Expected ## v{version} - YYYY-MM-DD"
        )


def _get_release_notes():
    with Path("pyproject.toml").open("rb") as f:
        data = tomli.load(f)
        version = data["tool"]["poetry"]["version"]
    release_notes = []

    with Path("CHANGELOG.md").open() as lines:
        lines = dropwhile(lambda line: not line.startswith("## "), lines)
        heading = next(lines)
        _validate_heading_format(heading, version)
        assert next(lines) == "\n", "Expected empty line after release heading"
        release_notes = list(takewhile(lambda line: not line.startswith("## "), lines))

    return "".join(release_notes).rstrip()


if __name__ == "__main__":
    print(_get_release_notes())  # noqa: T201
