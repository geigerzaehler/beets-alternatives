import os
import shutil
import sys
import tempfile
from concurrent import futures
from contextlib import contextmanager
from io import StringIO
from unittest import TestCase
from zlib import crc32

import beets
import beets.library
from beets import logging, plugins, ui, util
from beets.library import Item
from beets.util import MoveOperation, bytestring_path, displayable_path, syspath
from mediafile import MediaFile
from mock import patch

import beetsplug.alternatives as alternatives
import beetsplug.convert as convert

logging.getLogger("beets").propagate = True


class LogCapture(logging.Handler):
    def __init__(self):
        super(LogCapture, self).__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(str(record.msg))


@contextmanager
def capture_log(logger="beets"):
    capture = LogCapture()
    log = logging.getLogger(logger)
    log.addHandler(capture)
    try:
        yield capture.messages
    finally:
        log.removeHandler(capture)


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
    sys.stdout = capture = StringIO()
    try:
        yield sys.stdout
    finally:
        sys.stdout = org
        print(capture.getvalue())


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


class Assertions(TestCase):
    def assertFileTag(self, path, tag):
        self.assertIsFile(path)
        with open(syspath(path), "rb") as f:
            f.seek(-5, os.SEEK_END)
            self.assertEqual(f.read(), tag)

    def assertNotFileTag(self, path, tag):
        self.assertIsFile(path)
        with open(syspath(path), "rb") as f:
            f.seek(-5, os.SEEK_END)
            self.assertNotEqual(f.read(), tag)

    def assertIsFile(self, path):
        self.assertTrue(
            os.path.isfile(syspath(path)),
            msg="Path is not a file: {0}".format(displayable_path(path)),
        )

    def assertIsNotFile(self, path):
        """Asserts that `path` is neither a regular file (``os.path.isfile``,
        follows symlinks and returns False for a broken symlink) nor a symlink
        (``os.path.islink``, returns True for both valid and broken symlinks).
        """
        self.assertFalse(
            os.path.isfile(syspath(path)),
            msg="Path is a file: {0}".format(displayable_path(path)),
        )
        self.assertFalse(
            os.path.islink(syspath(path)),
            msg="Path is a symlink: {0}".format(displayable_path(path)),
        )

    def assertSymlink(self, link, target, absolute=True):
        self.assertTrue(
            os.path.islink(syspath(link)),
            msg="Path is not a symbolic link: {0}".format(displayable_path(link)),
        )
        self.assertTrue(
            os.path.isfile(syspath(target)),
            msg="Path is not a file: {0}".format(displayable_path(link)),
        )
        pre_link_target = bytestring_path(os.readlink(syspath(link)))
        link_target = os.path.join(os.path.dirname(link), pre_link_target)
        self.assertTrue(
            util.samefile(target, link_target),
            msg="Symlink points to {} instead of {}".format(
                displayable_path(link_target), displayable_path(target)
            ),
        )

        if absolute:
            self.assertTrue(
                os.path.isabs(pre_link_target),
                msg="Symlink {} is not absolute".format(
                    displayable_path(pre_link_target)
                ),
            )
        else:
            self.assertFalse(
                os.path.isabs(pre_link_target),
                msg="Symlink {} is not relative".format(
                    displayable_path(pre_link_target)
                ),
            )


class MediaFileAssertions(TestCase):
    def assertHasEmbeddedArtwork(self, path, compare_file=None):
        mediafile = MediaFile(syspath(path))
        self.assertIsNotNone(mediafile.art, msg="MediaFile has no embedded artwork")
        if compare_file:
            with open(syspath(compare_file), "rb") as compare_fh:
                crc_is = crc32(
                    mediafile.art  # pyright: ignore[reportGeneralTypeIssues]
                )
                crc_expected = crc32(compare_fh.read())
                self.assertEqual(
                    crc_is,
                    crc_expected,
                    msg="MediaFile has embedded artwork, but "
                    "content (CRC32: {}) doesn't match "
                    "expectations (CRC32: {}).".format(crc_is, crc_expected),
                )

    def assertHasNoEmbeddedArtwork(self, path):
        mediafile = MediaFile(syspath(path))
        self.assertIsNone(mediafile.art, msg="MediaFile has embedded artwork")

    def assertMediaFileFields(self, path, **kwargs):
        mediafile = MediaFile(syspath(path))
        for k, v in kwargs.items():
            actual = getattr(mediafile, k)
            self.assertTrue(
                actual == v,
                msg="MediaFile has tag {k}='{actual}' "
                "instead of '{expected}'".format(k=k, actual=actual, expected=v),
            )


class TestHelper(Assertions, MediaFileAssertions):
    def setUp(self, mock_worker=True):
        """Setup required for running test. Must be called before
        running any tests.

        If ``mock_worker`` is ``True`` the simple non-threaded
        ``MockedWorker`` is used to run file conversion commands. In
        particular, in contrast to the actual conversion routine from the
        ``convert`` plugin, it will not attempt to write tags to the output
        files. Thus, the 'converted' files need not be valid audio files.
        """
        if mock_worker:
            patcher = patch("beetsplug.alternatives.Worker", new=MockedWorker)
            patcher.start()
            self.addCleanup(patcher.stop)

        self._tempdirs = []
        plugins._classes = set([alternatives.AlternativesPlugin, convert.ConvertPlugin])
        self.setup_beets()

    def tearDown(self):
        self.unload_plugins()
        for tempdir in self._tempdirs:
            shutil.rmtree(syspath(tempdir))

    def mkdtemp(self):
        # This return a str path, i.e. Unicode on Python 3. We need this in
        # order to put paths into the configuration.
        path = tempfile.mkdtemp()
        self._tempdirs.append(path)
        return path

    def setup_beets(self):
        self.addCleanup(self.teardown_beets)
        os.environ["BEETSDIR"] = self.mkdtemp()

        self.config = beets.config
        self.config.clear()
        self.config.read()

        self.config["plugins"] = []
        self.config["verbose"] = True
        self.config["ui"]["color"] = False
        self.config["threaded"] = False
        self.config["import"]["copy"] = False

        libdir = self.mkdtemp()
        self.config["directory"] = libdir
        self.libdir = bytestring_path(libdir)

        self.lib = beets.library.Library(
            ":memory:", self.libdir  # pyright: ignore[reportGeneralTypeIssues]
        )
        self.fixture_dir = os.path.join(
            bytestring_path(os.path.dirname(__file__)), b"fixtures"
        )

        self.IMAGE_FIXTURE1 = os.path.join(self.fixture_dir, b"image.png")
        self.IMAGE_FIXTURE2 = os.path.join(self.fixture_dir, b"image_black.png")

    def teardown_beets(self):
        del self.lib._connections
        if "BEETSDIR" in os.environ:
            del os.environ["BEETSDIR"]
        self.config.clear()
        beets.config.read(user=False, defaults=True)

    def set_paths_config(self, conf):
        self.lib.path_formats = conf.items()

    def unload_plugins(self):
        for plugin in plugins._classes:
            plugin.listeners = None
            plugins._classes = set()
            plugins._instances = {}

    def runcli(self, *args):
        # TODO mock stdin
        with capture_stdout() as out:
            try:
                ui._raw_main(list(args), self.lib)
            except ui.UserError as u:
                # TODO remove this and handle exceptions in tests
                print(u.args[0])
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

    def get_path(self, item, path_key="alt.myexternal") -> bytes:
        path = alternatives.External._get_path(item, path_key)
        assert path is not None
        return path


class MockedWorker(alternatives.Worker):
    def __init__(self, fn, max_workers=None):
        self._tasks = set()
        self._fn = fn

    def submit(self, *args, **kwargs):
        fut = futures.Future()
        res = self._fn(*args, **kwargs)
        fut.set_result(res)
        # try:
        #     res = fn(*args, **kwargs)
        # except Exception as e:
        #     fut.set_exception(e)
        # else:
        #     fut.set_result(res)
        self._tasks.add(fut)
        return fut

    def shutdown(self, wait=True):
        pass
