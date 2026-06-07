# Test guidance

## Layout

- `cli_test.py` — the entire suite. Tests drive the plugin through the beets CLI
  (`runcli`) rather than calling classes directly.
- `helper.py` — `TestHelper` base class, fixtures, fixture media, and `assert_*`
  helpers. `TestHelper` sets up a temporary beets library and config per test. Add
  reusable assertions/builders here, not inline in tests.
- `conftest.py` — installs a typeguard import hook on `beetsplug.alternatives`, so
  type annotations are checked at runtime during tests. Keep annotations accurate
  or tests fail with `TypeCheckError`.

## Writing a test

- Subclass `TestHelper`. Group related tests in a class with an `autouse` fixture
  that sets `self.config["alternatives"]`.
- Fixtures that configure state must take `_setup: None` as a parameter so they run
  after the base `_setup` fixture. This is the project convention — the pyright
  "not accessed" hints on `_setup`/`tmp_path` are expected, not errors.
- Build library state with `add_track` / `add_album` (singletons vs. albums) and
  `add_external_track` / `add_external_album` (add + run update in one step).
- Invoke the plugin with `self.runcli("alt", "update", "<name>")`. `runcli` bumps
  `self.lib.revision`, so call `item.load()` afterwards to read fresh DB state.
- Read an item's alternate path with `self.get_path(item)` (defaults to the
  `alt.myexternal` key).

## Gotchas

- **`removable` defaults to `True`.** If the collection `directory` does not already
  exist, `alt update` prompts to create it and hangs without stdin. Either set
  `"removable": False`, pass `--create`, or wrap the call in `control_stdin("y")`.
- **`item.path` is `bytes`.** Convert with `Path(str(item.path, "utf8"))`.
- **mtime granularity.** Comparisons rely on mtimes; use `touch_art` and `sleep(0.1)`
  where the existing tests do to avoid flakiness on coarse filesystems.
- **Doctests run.** `pytest` uses `--doctest-modules`; docstring examples in
  `helper.py` execute as tests.
- **Warnings are errors** (`filterwarnings = error`); a new warning fails the suite.
