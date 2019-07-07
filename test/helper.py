import sys
import os
import tempfile
import six
import shutil
from contextlib import contextmanager
from six import StringIO
from concurrent import futures
from zlib import crc32
from unittest import TestCase

from mock import patch

import beets
from beets import logging
from beets import plugins
from beets import ui
from beets import util
from beets.library import Item
from beets.mediafile import MediaFile
from beets.util import (
    MoveOperation,
    syspath,
    bytestring_path,
    displayable_path,
)

from beetsplug import alternatives
from beetsplug import convert


logging.getLogger('beets').propagate = True


class LogCapture(logging.Handler):

    def __init__(self):
        super(LogCapture, self).__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(six.text_type(record.msg))


@contextmanager
def capture_log(logger='beets'):
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
    if six.PY2:  # StringIO encoding attr isn't writable in python >= 3
        sys.stdout.encoding = 'utf-8'
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
    if six.PY2:  # StringIO encoding attr isn't writable in python >= 3
        sys.stdin.encoding = 'utf-8'
    try:
        yield sys.stdin
    finally:
        sys.stdin = org


def _convert_args(args):
    """Convert args to bytestrings for Python 2 and convert them to strings
       on Python 3.
    """
    for i, elem in enumerate(args):
        if six.PY2:
            if isinstance(elem, six.text_type):
                args[i] = elem.encode(util.arg_encoding())
        else:
            if isinstance(elem, bytes):
                args[i] = elem.decode(util.arg_encoding())

    return args


class Assertions(object):

    def assertFileTag(self, path, tag):
        self.assertIsFile(path)
        with open(syspath(path), 'rb') as f:
            f.seek(-5, os.SEEK_END)
            self.assertEqual(f.read(), tag)

    def assertNotFileTag(self, path, tag):
        self.assertIsFile(path)
        with open(syspath(path), 'rb') as f:
            f.seek(-5, os.SEEK_END)
            self.assertNotEqual(f.read(), tag)

    def assertIsFile(self, path):
        self.assertTrue(os.path.isfile(syspath(path)),
                        msg=u'Path is not a file: {0}'.format(
                            displayable_path(path)
                        ))

    def assertIsNotFile(self, path):
        self.assertFalse(os.path.isfile(syspath(path)),
                         msg=u'Path is a file: {0}'.format(
                             displayable_path(path)
                        ))

    def assertSymlink(self, link, target):
        self.assertTrue(os.path.islink(syspath(link)),
                        msg=u'Path is not a symbolic link: {0}'.format(
                            displayable_path(link)
                        ))
        self.assertTrue(os.path.isfile(syspath(target)),
                        msg=u'Path is not a file: {0}'.format(
                            displayable_path(link)
                        ))
        link_target = bytestring_path(os.readlink(syspath(link)))
        link_target = os.path.join(os.path.dirname(link), link_target)
        self.assertTrue(util.samefile(target, link_target),
                        msg=u'Symlink points to {} instead of {}'.format(
                                displayable_path(link_target),
                                displayable_path(target)
                            ))

    def assertIsNotSymlink(self, link):
        # This is not redundant with assertIsFile, because the latter follows
        # symlinks. Note that os.path.exists would return False for a broken
        # symlink.
        self.assertFalse(os.path.lexists(syspath(link)),
                         msg=u'Path is a file or symbolic link: {0}'.format(
                             displayable_path(link)
                         ))


class MediaFileAssertions(object):

    def assertHasEmbeddedArtwork(self, path, compare_file=None):
        mediafile = MediaFile(syspath(path))
        self.assertIsNotNone(mediafile.art,
                             msg=u'MediaFile has no embedded artwork')
        if compare_file:
            with open(syspath(compare_file), 'rb') as compare_fh:
                crc_is = crc32(mediafile.art)
                crc_expected = crc32(compare_fh.read())
                self.assertEqual(
                        crc_is, crc_expected,
                        msg=u"MediaFile has embedded artwork, but "
                            u"content (CRC32: {}) doesn't match "
                            u"expectations (CRC32: {}).".format(
                                crc_is, crc_expected
                                )
                            )

    def assertHasNoEmbeddedArtwork(self, path):
        mediafile = MediaFile(syspath(path))
        self.assertIsNone(mediafile.art,
                          msg=u'MediaFile has embedded artwork')

    def assertMediaFileFields(self, path, **kwargs):
        mediafile = MediaFile(syspath(path))
        for k, v in kwargs.items():
            actual = getattr(mediafile, k)
            self.assertTrue(actual == v,
                            msg=u"MediaFile has tag {k}='{actual}' "
                                u"instead of '{expected}'".format(
                                    k=k, actual=actual, expected=v)
                            )


class TestHelper(TestCase, Assertions, MediaFileAssertions):

    def setUp(self, mock_worker=True):
        if mock_worker:
            patcher = patch('beetsplug.alternatives.Worker', new=MockedWorker)
            patcher.start()
            self.addCleanup(patcher.stop)

        self._tempdirs = []
        plugins._classes = set([alternatives.AlternativesPlugin,
                                convert.ConvertPlugin])
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
        os.environ['BEETSDIR'] = self.mkdtemp()

        self.config = beets.config
        self.config.clear()
        self.config.read()

        self.config['plugins'] = []
        self.config['verbose'] = True
        self.config['ui']['color'] = False
        self.config['threaded'] = False
        self.config['import']['copy'] = False

        libdir = self.mkdtemp()
        self.config['directory'] = libdir
        self.libdir = bytestring_path(libdir)

        self.lib = beets.library.Library(':memory:', self.libdir)
        self.fixture_dir = os.path.join(
                bytestring_path(os.path.dirname(__file__)),
                b'fixtures')

        self.IMAGE_FIXTURE1 = os.path.join(self.fixture_dir,
                                           b'image.png')
        self.IMAGE_FIXTURE2 = os.path.join(self.fixture_dir,
                                           b'image_black.png')

    def teardown_beets(self):
        del self.lib._connections
        if 'BEETSDIR' in os.environ:
            del os.environ['BEETSDIR']
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
                ui._raw_main(_convert_args(list(args)), self.lib)
            except ui.UserError as u:
                # TODO remove this and handle exceptions in tests
                print(u.args[0])
        return out.getvalue()

    def lib_path(self, path):
        return os.path.join(self.libdir,
                            path.replace(b'/', bytestring_path(os.sep)))

    def item_fixture_path(self, fmt):
        assert(fmt in 'mp3 m4a ogg'.split())
        return os.path.join(self.fixture_dir,
                            bytestring_path('min.' + fmt.lower()))

    def add_album(self, **kwargs):
        values = {
            'title': 'track 1',
            'artist': 'artist 1',
            'album': 'album 1',
            'format': 'mp3',
        }
        values.update(kwargs)
        item = Item.from_path(self.item_fixture_path(values.pop('format')))
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
            'title': 'track 1',
            'artist': 'artist 1',
            'album': 'album 1',
            'format': 'mp3',
        }
        values.update(kwargs)

        item = Item.from_path(self.item_fixture_path(values.pop('format')))
        item.add(self.lib)
        item.update(values)
        item.move(MoveOperation.COPY)
        item.write()
        return item

    def add_external_track(self, ext_name, **kwargs):
        kwargs[ext_name] = 'true'
        item = self.add_track(**kwargs)
        self.runcli('alt', 'update', ext_name)
        item.load()
        return item

    def add_external_album(self, ext_name, **kwargs):
        album = self.add_album(**kwargs)
        album[ext_name] = 'true'
        album.store()
        self.runcli('alt', 'update', ext_name)
        album.load()
        return album

    def get_path(self, item, path_key='alt.myexternal'):
        return alternatives.External._get_path(item, path_key)


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

    def shutdown(wait=True):
        pass
