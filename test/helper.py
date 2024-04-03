import os
import sys
from concurrent import futures
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Optional
from zlib import crc32

import beets
import beets.library
import pytest
from beets import logging, plugins, ui, util
from beets.library import Item
from beets.util import MoveOperation, bytestring_path, displayable_path, syspath
from mediafile import MediaFile

import beetsplug.alternatives as alternatives
import beetsplug.convert as convert

beetsLogger = logging.getLogger("beets")
beetsLogger.propagate = True
for h in beetsLogger.handlers:
    beetsLogger.removeHandler(h)


@contextmanager
def capture_stdout():
    """Save stdout in a StringIO.

    >>> with capture_stdout() as output:
    ...     print('spam')
    ...
    >>> output.getvalue()
    'spam'
    """
    org = sys.stdout
    sys.stdout = StringIO()
    try:
        yield sys.stdout
    finally:
        sys.stdout = org


@contextmanager
def control_stdin(input=None):
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


def assert_file_tag(path, tag: bytes):
    assert_is_file(path)
    with open(syspath(path), "rb") as f:
        f.seek(-5, os.SEEK_END)
        assert f.read() == tag


def assert_not_file_tag(path, tag: bytes):
    assert_is_file(path)
    with open(syspath(path), "rb") as f:
        f.seek(-5, os.SEEK_END)
        assert f.read() != tag


def assert_is_file(path):
    assert os.path.isfile(
        syspath(path)
    ), f"Path is not a file: {displayable_path(path)}"


def assert_is_not_file(path):
    """Asserts that `path` is neither a regular file (``os.path.isfile``,
    follows symlinks and returns False for a broken symlink) nor a symlink
    (``os.path.islink``, returns True for both valid and broken symlinks).
    """
    assert not os.path.isfile(
        syspath(path)
    ), f"Path is a file: {displayable_path(path)}"
    assert not os.path.islink(
        syspath(path)
    ), f"Path is a symlink: {displayable_path(path)}"


def assert_symlink(link, target, absolute: bool = True):
    assert os.path.islink(
        syspath(link)
    ), f"Path is not a symbolic link: {displayable_path(link)}"
    assert os.path.isfile(
        syspath(target)
    ), f"Path is not a file: {displayable_path(link)}"
    pre_link_target = bytestring_path(os.readlink(syspath(link)))
    link_target = os.path.join(os.path.dirname(link), pre_link_target)
    assert util.samefile(
        target, link_target
    ), f"Symlink points to {displayable_path(link_target)} instead of {displayable_path(target)}"

    if absolute:
        assert os.path.isabs(
            pre_link_target
        ), f"Symlink {displayable_path(pre_link_target)} is not absolute"
    else:
        assert not os.path.isabs(
            pre_link_target
        ), f"Symlink {displayable_path(pre_link_target)} is not relative"


def assert_has_embedded_artwork(path, compare_file=None):
    mediafile = MediaFile(syspath(path))
    assert mediafile.art is not None, "MediaFile has no embedded artwork"
    if compare_file:
        with open(syspath(compare_file), "rb") as compare_fh:  # noqa: FURB101
            crc_is = crc32(mediafile.art)  # pyright: ignore[reportArgumentType]
            crc_expected = crc32(compare_fh.read())
            assert crc_is == crc_expected, (
                "MediaFile has embedded artwork, but "
                f"content (CRC32: {crc_is}) doesn't match "
                f"expectations (CRC32: {crc_expected})."
            )


def assert_has_not_embedded_artwork(path):
    mediafile = MediaFile(syspath(path))
    assert mediafile.art is None, "MediaFile has embedded artwork"


def assert_media_file_fields(path, **kwargs):
    mediafile = MediaFile(syspath(path))
    for k, v in kwargs.items():
        actual = getattr(mediafile, k)
        assert actual == v, f"MediaFile has tag {k}='{actual}' " f"instead of '{v}'"


class TestHelper:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("beetsplug.alternatives.Worker", MockedWorker)

        plugins._classes = {alternatives.AlternativesPlugin, convert.ConvertPlugin}

        self.config = beets.config
        self.config.clear()
        self.config.read()

        self.config["plugins"] = []
        self.config["verbose"] = True
        self.config["ui"]["color"] = False
        self.config["threaded"] = False
        self.config["import"]["copy"] = False

        libdir = str(tmp_path / "beets_lib")
        os.environ["BEETSDIR"] = libdir
        self.config["directory"] = libdir
        self.libdir = bytestring_path(libdir)

        self.lib = beets.library.Library(
            ":memory:",
            self.libdir,  # pyright: ignore[reportArgumentType]
        )
        self.fixture_dir = os.path.join(
            bytestring_path(os.path.dirname(__file__)), b"fixtures"
        )

        self.IMAGE_FIXTURE1 = os.path.join(self.fixture_dir, b"image.png")
        self.IMAGE_FIXTURE2 = os.path.join(self.fixture_dir, b"image_black.png")
        yield
        for plugin in plugins._classes:
            plugin.listeners = None
            plugins._classes = set()
            plugins._instances = {}

    def runcli(self, *args):
        # TODO mock stdin
        with capture_stdout() as out:
            ui._raw_main(list(args), self.lib)
        return out.getvalue()

    def lib_path(self, path):
        return os.path.join(self.libdir, path.replace(b"/", bytestring_path(os.sep)))

    def item_fixture_path(self, fmt):
        assert fmt in "mp3 m4a ogg".split()
        return os.path.join(self.fixture_dir, bytestring_path("min." + fmt.lower()))

    def add_album(self, **kwargs):
        values = {
            "title": "track 1",
            "artist": "artist 1",
            "album": "album 1",
            "format": "mp3",
        }
        values.update(kwargs)
        item = Item.from_path(self.item_fixture_path(values.pop("format")))
        item.add(self.lib)
        item.update(values)
        item.move(MoveOperation.COPY)
        item.write()
        album = self.lib.add_album([item])
        album.albumartist = item.artist
        album.store()
        return album

    def add_track(self, **kwargs):
        values = {
            "title": "track 1",
            "artist": "artist 1",
            "album": "album 1",
            "format": "mp3",
        }
        values.update(kwargs)

        item = Item.from_path(self.item_fixture_path(values.pop("format")))
        item.add(self.lib)
        item.update(values)
        item.move(MoveOperation.COPY)
        item.write()
        return item

    def add_external_track(self, ext_name, **kwargs):
        kwargs[ext_name] = "true"
        item = self.add_track(**kwargs)
        self.runcli("alt", "update", ext_name)
        item.load()
        return item

    def add_external_album(self, ext_name, **kwargs):
        album = self.add_album(**kwargs)
        album[ext_name] = "true"
        album.store()
        self.runcli("alt", "update", ext_name)
        album.load()
        return album

    def get_path(self, item, path_key="alt.myexternal") -> Optional[bytes]:
        try:
            return item[path_key].encode("utf8")
        except KeyError:
            return None


class MockedWorker(alternatives.Worker):
    def __init__(self, fn, max_workers=None):
        self._tasks = set()
        self._fn = fn

    def submit(self, *args, **kwargs):
        fut = futures.Future()
        res = self._fn(*args, **kwargs)
        fut.set_result(res)
        self._tasks.add(fut)
        return fut

    def shutdown(self, wait=True):
        pass
