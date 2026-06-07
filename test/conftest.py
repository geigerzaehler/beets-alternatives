# beets master no longer auto-registers the `beets.util.pipeline` submodule; the
# bundled convert plugin references `util.pipeline` at import time, so import it
# before any test imports `beetsplug.convert`.
import beets.util.pipeline  # pyright: ignore[reportUnusedImport]
import typeguard

typeguard.install_import_hook("beetsplug.alternatives")
