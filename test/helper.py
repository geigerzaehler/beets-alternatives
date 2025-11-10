# pyright: reportPrivateUsage=false

import os
import platform
import sys
from collections import defaultdict
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from zlib import crc32

import beets
import beets.library
import beets.plugins
import beetsplug.convert
import beetsplug.hook
import pytest
from beets import logging, ui
from beets.library import Album, Item
from beets.util import MoveOperation
from mediafile import MediaFile

import beetsplug.alternatives

beetsLogger = logging.getLogger("beets")
beetsLogger.propagate = True
for h in beetsLogger.handlers:
    beetsLogger.removeHandler(h)

_beets_version = tuple(map(int, beets.__version__.split(".")[0:3]))


@contextmanager
def capture_stdout():
    r"""Collect stdout in a StringIO while still outputting it.

    >>> with capture_stdout() as output:
    ...     print('spam')
    ...
    spam
    >>> output.getvalue()
    'spam\n'
    """
    org = sys.stdout
    buffer = StringIO()
    sys.stdout = buffer
    try:
        yield sys.stdout
    finally:
        sys.stdout = org
        sys.stdout.write(buffer.getvalue())


@contextmanager
def control_stdin(input: str | None = None):
    """Sends ``input`` to stdin.

    >>> with control_stdin('yes'):
    ...     input()
    'yes'
    """
    org = sys.stdin
    sys.stdin = StringIO(input)
    try:
        yield sys.stdin
    finally:
        sys.stdin = org


def assert_file_tag(path: Path, tag: bytes):
    with path.open("rb") as f:
        f.seek(-5, os.SEEK_END)
        assert f.read() == tag


def assert_not_file_tag(path: Path, tag: bytes):
    with path.open("rb") as f:
        f.seek(-5, os.SEEK_END)
        assert f.read() != tag


def assert_is_not_file(path: Path):
    """Asserts that `path` is neither a regular file (``os.path.isfile``,
    follows symlinks and returns False for a broken symlink) nor a symlink
    (``os.path.islink``, returns True for both valid and broken symlinks).
    """
    assert not path.is_file()
    assert not path.is_symlink()


def assert_symlink(link: Path, target: Path, absolute: bool = True):
    assert link.is_symlink()
    assert link.resolve() == target.resolve()
    assert link.readlink().is_absolute() == absolute


def assert_has_embedded_artwork(path: Path, compare_file: Path | None = None):
    mediafile = MediaFile(path)
    assert mediafile.art is not None, "MediaFile has no embedded artwork"
    if compare_file:
        with compare_file.open("rb") as compare_fh:
            crc_is = crc32(mediafile.art)  # pyright: ignore[reportArgumentType]
            crc_expected = crc32(compare_fh.read())
            assert crc_is == crc_expected, (
                "MediaFile has embedded artwork, but "
                f"content (CRC32: {crc_is}) doesn't match "
                f"expectations (CRC32: {crc_expected})."
            )

def assert_has_artwork(path: Path, compare_file: Path | None = None):
    assert path.is_file()
    if compare_file:
        with compare_file.open("rb") as compare_fh, path.open("rb") as path_fh:
            crc_is = crc32(path_fh.read())
            crc_expected = crc32(compare_fh.read())
            assert crc_is == crc_expected, (
                "artwork file exists, but "
                f"content (CRC32: {crc_is}) doesn't match "
                f"expectations (CRC32: {crc_expected})."
            )

def assert_has_not_embedded_artwork(path: Path):
    mediafile = MediaFile(path)
    assert mediafile.art is None, "MediaFile has embedded artwork"


def assert_media_file_fields(path: Path, **kwargs: str):
    mediafile = MediaFile(path)
    for k, v in kwargs.items():
        actual = getattr(mediafile, k)
        assert actual == v, f"MediaFile has tag {k}='{actual}' instead of '{v}'"


class TestHelper:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.config = beets.config
        self.config.clear()
        self.config.read()

        self.config["plugins"] = []
        self.config["verbose"] = True
        self.config["ui"]["color"] = False
        self.config["threaded"] = False
        self.config["import"]["copy"] = False

        self.libdir = tmp_path / "beets_lib"
        os.environ["BEETSDIR"] = str(self.libdir)
        self.config["directory"] = str(self.libdir)

        self.lib = beets.library.Library(
            ":memory:",
            str(self.libdir),
        )
        self.fixture_dir = Path(__file__).parent / "fixtures"

        self.IMAGE_FIXTURE1 = self.fixture_dir / "image.png"
        self.IMAGE_FIXTURE2 = self.fixture_dir / "image_black.png"

        if _beets_version > (2, 3, 1):
            beets.plugins._instances = [
                beetsplug.alternatives.AlternativesPlugin(),
                beetsplug.convert.ConvertPlugin(),
                beetsplug.hook.HookPlugin(),
            ]
        else:
            beets.plugins._classes = {  # type: ignore (compatibility with beets<2.4)
                beetsplug.alternatives.AlternativesPlugin,
                beetsplug.convert.ConvertPlugin,
                beetsplug.hook.HookPlugin,
            }
            beets.plugins._instances = {}

        yield

        if _beets_version > (2, 3, 1):
            beets.plugins.BeetsPlugin.listeners = defaultdict(list)
        else:
            for plugin in beets.plugins._classes:  # type: ignore (compatibility with beets<2.4)
                # Instantiating a plugin will modify register event listeners which
                # are stored in a class variable
                plugin.listeners = None  # type: ignore (compatibility with beets<2.4)

    @pytest.fixture
    def event_log(self, tmp_path: Path, _setup: None) -> Path:
        """Add hook for the `alternatives.update` event that logs the event to the
        returned path.

        The format for the events is `{collection}, {action}, {item.title}`.
        """

        hook_log = tmp_path / "update-event.log"

        log_line = "{collection}, {action}, {path}, {item.title}"
        if platform.system() == "Windows":
            command = f'powershell -Command "Add-Content -Path \\"{hook_log}\\" -Value \\"{log_line}\\""'
        else:
            command = f"bash -c 'echo \"{log_line}\" >> {hook_log}'"

        self.config["hook"]["hooks"] = [
            {
                "event": "alternatives.item_updated",
                "command": command,
            },
        ]

        if _beets_version > (2, 3, 1):
            beets.plugins._instances.append(  # type: ignore
                beetsplug.hook.HookPlugin(),
            )

        else:
            beets.plugins._classes.add(  # type: ignore (compatibility with beets<2.4)
                beetsplug.hook.HookPlugin,
            )

        return hook_log

    def runcli(self, *args: str) -> str:
        # TODO mock stdin
        with capture_stdout() as out:
            ui._raw_main(list(args), self.lib)
        return out.getvalue()

    def item_fixture_path(self, fmt: str):
        assert fmt in {"mp3", "m4a", "ogg"}
        return self.fixture_dir / f"min.{fmt}"

    def add_album(self, **kwargs: str):
        values = {
            "title": "track 1",
            "artist": "artist 1",
            "album": "album 1",
            "format": "mp3",
        }
        values.update(kwargs)
        item = Item.from_path(str(self.item_fixture_path(values.pop("format"))))
        item.add(self.lib)
        item.update(values)
        item.move(MoveOperation.COPY)
        item.write()
        album = self.lib.add_album([item])
        album.albumartist = item.artist
        album.store()
        return album

    def add_track(self, **kwargs: str):
        values = {
            "title": "track 1",
            "artist": "artist 1",
            "album": "album 1",
            "format": "mp3",
        }
        values.update(kwargs)

        item = Item.from_path(str(self.item_fixture_path(values.pop("format"))))
        item.add(self.lib)
        item.update(values)
        item.move(MoveOperation.COPY)
        item.write()
        return item

    def add_external_track(self, ext_name: str, **kwargs: str):
        kwargs[ext_name] = "true"
        item = self.add_track(**kwargs)
        self.runcli("alt", "update", ext_name)
        item.load()
        return item

    def add_external_album(self, ext_name: str, **kwargs: str):
        album = self.add_album(**kwargs)
        album[ext_name] = "true"
        album.store()
        self.runcli("alt", "update", ext_name)
        album.load()
        return album

    def get_path(self, item: Item, path_key: str = "alt.myexternal") -> Path:
        return Path(item[path_key])

    def get_album_path(self, album: Album, path_key: str = "alt.myexternal") -> Path:
        item = album.items().get()
        if item:
            head, _ = os.path.split(self.get_path(item, path_key=path_key))
            return head
        else:
            return None


def convert_command(tag: str) -> str:
    """Return a convert shell command that copies the file and adds a tag to the files end."""

    system = platform.system()
    if system == "Windows":
        return (
            'powershell -Command "'
            "Copy-Item -Path '$source' -Destination '$dest';"
            f"Add-Content -Path '$dest' -Value {tag} -NoNewline"
            '"'
        )
    elif system == "Linux" or system == "Darwin":
        return f"bash -c \"cp '$source' '$dest'; printf {tag} >> '$dest'\""
    else:
        raise Exception(f"Unsupported system: {system}")
