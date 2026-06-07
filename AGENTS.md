# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) and other coding agents
when working with code in this repository.

`AGENTS.md` is the canonical instructions file; `CLAUDE.md` is a symlink to it. Edit
`AGENTS.md`.

## Overview

`beets-alternatives` is a [beets](https://beets.io) plugin that maintains alternate
versions of a music library in separate locations — e.g. transcoded copies for a
portable player, or symlinked collections. Users define named *collections* in
beets config; `beet alt update <name>` syncs each collection to match a query.

## Commands

This project uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # install deps
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pyright .             # type-check (strict mode)
uv run pytest                # run tests with coverage + doctests
uv run pytest test/cli_test.py::TestDoc::test_external   # run a single test
```

`pytest` runs with `--doctest-modules`, so docstring examples (e.g. in
`test/helper.py`) are executed as tests. Warnings are errors (`filterwarnings = error`).

## Architecture

The plugin is a single module, `beetsplug/alternatives.py`. Key pieces:

- **`AlternativesPlugin`** — beets entry point. Registers the `alt` subcommand and
  dispatches `update` / `list-tracks`. `alternative()` is the factory that reads a
  collection's config and returns the right view class.
- **`Config`** — parses one collection's confuse config. The `formats` option drives
  the view type: `"link"` → symlinks, a format list → transcode, empty → plain copy.
- **View classes** — the sync strategies, all subclassing `External`:
  - `External` — copies files verbatim; the base sync engine.
  - `ExternalConvert` — transcodes via the bundled `beetsplug.convert` plugin when
    an item's format isn't in the allowed list. Overrides `_converter` / `destination`.
  - `SymlinkView` — creates absolute or relative symlinks instead of copying.
- **`Action` enum + `_items_actions()`** — the sync core. For each library item it
  computes a list of actions (`ADD`, `REMOVE`, `MOVE`, `WRITE`, `SYNC_ART`) by
  comparing the query, the stored alternate path, and mtimes. `update()` then
  executes them. This diff-based model is central — changes to sync behavior happen here.
- **`Worker`** — a `ThreadPoolExecutor` for parallel transcoding. Conversions are
  submitted async and finalized (tag-write, art-embed, store) as they complete via a
  done `queue.Queue`. Worker count comes from beets' `convert.threads` config.

### State tracking

Each collection stores the alternate file's path on the beets `Item` as a flexible
attribute `alt.<collection_id>` (`path_key`). This is how the plugin knows what's
already in a collection across runs. There is no separate database.

### Events

After each item change the plugin sends the `alternatives.item_updated` beets event
(`_send_item_updated`) so other plugins/hooks can react.

## Conventions

### Type checking

- Annotate everything, including private helpers. Strict pyright plus the typeguard
  test hook mean wrong annotations fail tests, not just lint.
- Put `@override` (from `typing_extensions`) on every overriding method.
- Narrow `confuse` reads with `assert isinstance(...)` right after `.get(...)`;
  confuse returns `object`, and the assert is what gives pyright the concrete type.
- Use targeted `# pyright: ignore[ruleName]`, never a blanket `# type: ignore`.

### General

- Minimum supported Python is 3.10. Guard newer-version-only APIs with a fallback
  (e.g. `Path.relative_to(walk_up=True)` needs a shim below 3.12).
- Strict pyright; prefer `pathlib` over `os` (enforced by ruff `PTH`). `print()` is
  flagged (`T20`) — use beets' `print_`.
- The CHANGELOG has an "Upcoming" section; add notable changes there. Release steps
  are in `DEVELOPING.md`.
