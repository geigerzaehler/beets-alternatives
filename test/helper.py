# pyright: reportPrivateUsage=false

import os
import platform
import sys
from collections import defaultdict
from contextlib import contextmanager
from io import StringIO
from itertools import zip_longest
from pathlib import Path
from zlib import crc32

import beets
import beets.library
import beets.plugins
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
            crc_is = crc32(mediafile.art)
            crc_expected = crc32(compare_fh.read())
            assert crc_is == crc_expected, (
                "MediaFile has embedded artwork, but "
                f"content (CRC32: {crc_is}) doesn't match "
                f"expectations (CRC32: {crc_expected})."
            )


def assert_same_file_content(a: Path, b: Path):
    assert a.is_file()
    assert b.is_file()
    with b.open("rb") as compare_fh, a.open("rb") as path_fh:
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


def assert_has_artwork(path: Path, compare_embedded: bool, compare_external: bool,
                       compare_file: Path | None):
    if compare_embedded and compare_file:
        assert_has_embedded_artwork(path, compare_file)
    else:
        assert_has_not_embedded_artwork(path)

    art_files = list(path.parent.glob("COVER*"))
    if compare_external and compare_file:
        assert art_files, "Expected art file but none found"
        assert_same_file_content(art_files[0], compare_file)
    else:
        assert not art_files, f"Unexpected art files: {art_files}"


def assert_media_file_fields(path: Path, **kwargs: str):
    mediafile = MediaFile(path)
    for k, v in kwargs.items():
        actual = getattr(mediafile, k)
        assert actual == v, f"MediaFile has tag {k}='{actual}' instead of '{v}'"


class _LibraryTracker(beets.plugins.BeetsPlugin):
    def __init__(self):
        super().__init__()
        self._opened: list[beets.library.Library] = []
        self.register_listener("library_opened", self._on_library_opened)

    def _on_library_opened(self, lib: beets.library.Library):
        self._opened.append(lib)

    def close_all(self):
        for lib in self._opened:
            lib._close()
        self._opened.clear()


class TestHelper:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.config = beets.config
        self.config.clear()
        self.config.read()

        self.config["library"] = str(tmp_path / "db.sqlite3")
        self.config["plugins"] = []
        self.config["verbose"] = True
        self.config["ui"]["color"] = False
        self.config["threaded"] = False
        self.config["import"]["copy"] = False

        self.libdir = tmp_path / "beets_lib"
        os.environ["BEETSDIR"] = str(self.libdir)
        self.config["directory"] = str(self.libdir)

        self.lib = beets.library.Library(
            self.config["library"].as_filename(),
            str(self.libdir),
        )
        self.fixture_dir = Path(__file__).parent / "fixtures"

        self.IMAGE_FIXTURE1 = self.fixture_dir / "image.png"
        self.IMAGE_FIXTURE2 = self.fixture_dir / "image_black.png"

        # Record checksums for all fixture files to ensure tests don't modify them
        self._fixture_checksums = {}
        for fixture_file in self.fixture_dir.rglob("*"):
            if fixture_file.is_file():
                relative_path = fixture_file.relative_to(self.fixture_dir)
                self._fixture_checksums[str(relative_path)] = crc32(fixture_file.read_bytes())

        self._lib_tracker = _LibraryTracker()

        beets.plugins._instances = [
            beetsplug.alternatives.AlternativesPlugin(),
            beetsplug.hook.HookPlugin(),
            self._lib_tracker,
        ]

        yield

        # Verify fixture files weren't modified during the test
        fixtures_modified = []
        for fixture_file in self.fixture_dir.rglob("*"):
            if fixture_file.is_file():
                relative_path = fixture_file.relative_to(self.fixture_dir)
                path_str = str(relative_path)
                if crc32(fixture_file.read_bytes()) != self._fixture_checksums[path_str]:
                    fixtures_modified.append(path_str)

        if fixtures_modified:
            raise AssertionError(
                f"Test modified fixture file(s): {', '.join(fixtures_modified)}"
            )

        self._lib_tracker.close_all()

        beets.plugins.BeetsPlugin.listeners = defaultdict(list)

        self.lib._close()

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

        beets.plugins._instances.append(
            beetsplug.hook.HookPlugin(),
        )

        return hook_log

    def runcli(self, *args: str) -> str:
        # TODO mock stdin
        with capture_stdout() as out:
            ui._raw_main(list(args))
        # _raw_main opens a separate library connection; increment revision
        # so that item.load() re-reads from DB instead of using cached state.
        self.lib.revision += 1
        return out.getvalue()

    def item_fixture_path(self, fmt: str):
        assert fmt in {"mp3", "m4a", "ogg"}
        return self.fixture_dir / f"min.{fmt}"

    def add_album(self, track_count=1, embed_art=None, external_art=None, **kwargs):
        assert track_count >= 1
        if embed_art is None:
            embed_art = []
        elif not isinstance(embed_art, list):
            embed_art = [embed_art]
        tracks = []
        for i, art in zip_longest(range(track_count), embed_art):
            if i is None:
                break
            track = self.add_track(title_no=i + 1, embed_art=art, **kwargs)
            tracks.append(track)

        album = self.lib.add_album(tracks)
        album.albumartist = tracks[0].artist
        if external_art is not None:
            album.set_art(external_art)
        album.store()
        return album

    def add_track(self, **kwargs: str):
        embed_art = kwargs.pop("embed_art", None)
        title_no = kwargs.pop("title_no", 1)
        artist_no = kwargs.pop("artist_no", 1)
        album_no = kwargs.pop("album_no", 1)
        values = {
            "title": f"track {title_no}",
            "artist": f"artist {artist_no}",
            "album": f"album {album_no}",
            "format": "mp3",
        }
        values.update(kwargs)

        item = Item.from_path(str(self.item_fixture_path(values.pop("format"))))
        item.add(self.lib)
        item.update(values)
        item.move(MoveOperation.COPY)
        item.write()

        if embed_art is not None:
            mf = MediaFile(str(item.path, "utf8"))
            with embed_art.open("rb") as f:
                mf.art = f.read()
            mf.save()

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
        assert item
        return self.get_path(item, path_key=path_key).parent


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
    elif system in {"Linux", "Darwin"}:
        return f"bash -c \"cp '$source' '$dest'; printf {tag} >> '$dest'\""
    else:
        raise RuntimeError(f"Unsupported system: {system}")


def touch_art(source: bytes, dest: Path):
    """`touch` the dest file, but don't set mtime to the current
    time since the tests run rather fast and item and art mtimes might
    end up identical if the filesystem has low mtime granularity or
    mtimes are cashed as laid out in
        https://stackoverflow.com/a/14393315/3451198
    Considering the interpreter startup time when running `beet alt
    update <name>` in a real use-case, this should not obscure any
    bugs.
    """
    item_mtime_alt = Path(str(source, "utf8")).stat().st_mtime
    os.utime(dest, (item_mtime_alt + 2, item_mtime_alt + 2))
