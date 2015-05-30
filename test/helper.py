import sys
import os
import tempfile
import logging
import shutil
from contextlib import contextmanager
from StringIO import StringIO
from concurrent import futures

import beets
from beets import plugins
from beets import ui
from beets.library import Item

from beetsplug import alternatives
from beetsplug import convert


logging.getLogger('beets').propagate = True


class LogCapture(logging.Handler):

    def __init__(self):
        super(LogCapture, self).__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(str(record.msg))


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
    org = sys.stdout
    sys.stdout = captured = StringIO()
    try:
        yield sys.stdout
    finally:
        sys.stdout = org
        sys.stdout.write(captured.getvalue())


@contextmanager
def control_stdin(input=None):
    org = sys.stdin
    sys.stdin = StringIO(input)
    sys.stdin.encoding = 'utf8'
    try:
        yield sys.stdin
    finally:
        sys.stdin = org


class Assertions(object):

    def assertFileTag(self, path, tag):
        self.assertIsFile(path)
        with open(path, 'r') as f:
            f.seek(-5, os.SEEK_END)
            self.assertEqual(f.read(), tag)

    def assertNotFileTag(self, path, tag):
        self.assertIsFile(path)
        with open(path, 'r') as f:
            f.seek(-5, os.SEEK_END)
            self.assertNotEqual(f.read(), tag)

    def assertIsFile(self, path):
        if not isinstance(path, unicode):
            path = unicode(path, 'utf8')
        self.assertTrue(os.path.isfile(path.encode('utf8')),
                        msg=u'Path is not a file: {0}'.format(path))

    def assertIsNotFile(self, path):
        if not isinstance(path, unicode):
            path = unicode(path, 'utf8')
        self.assertFalse(os.path.isfile(path.encode('utf8')),
                         msg=u'Path is a file: {0}'.format(path))

    def assertSymlink(self, link, target):
        self.assertTrue(os.path.islink(link),
                        msg=u'Path is not a symbolic link: {0}'.format(link))
        self.assertTrue(os.path.isfile(target),
                        msg=u'Path is not a file: {0}'.format(link))
        link_target = os.readlink(link)
        link_target = os.path.join(os.path.dirname(link), link_target)
        self.assertEqual(target, link_target)


class TestHelper(Assertions):

    def setUp(self):
        ThreadPoolMockExecutor.patch()
        self._tempdirs = []
        self.temp_dir = tempfile.mkdtemp()
        self._teardown_hooks = []
        plugins._classes = set([alternatives.AlternativesPlugin,
                                convert.ConvertPlugin])
        self.setup_beets()

    def tearDown(self):
        ThreadPoolMockExecutor.unpatch()
        for hook in self._teardown_hooks:
            hook()
        self.unload_plugins()
        for tempdir in self._tempdirs:
            shutil.rmtree(tempdir)

    def mkdtemp(self):
        path = tempfile.mkdtemp()
        self._tempdirs.append(path)
        return path

    def setup_beets(self):
        self._teardown_hooks.append(self.teardown_beets)
        os.environ['BEETSDIR'] = self.mkdtemp()

        self.config = beets.config
        self.config.clear()
        self.config.read()

        self.config['plugins'] = []
        self.config['verbose'] = True
        self.config['color'] = False
        self.config['threaded'] = False
        self.config['import']['copy'] = False

        self.libdir = self.mkdtemp()
        self.config['directory'] = self.libdir

        self.lib = beets.library.Library(':memory:', self.libdir)
        self.fixture_dir = os.path.join(os.path.dirname(__file__), 'fixtures')

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
                ui._raw_main(list(args), self.lib)
            except ui.UserError as u:
                # TODO remove this and handle exceptions in tests
                print(u.args[0])
        return out.getvalue()

    def lib_path(self, path):
        return os.path.join(self.libdir, path.replace('/', os.sep))

    def add_album(self, **kwargs):
        values = {
            'title': 'track 1',
            'artist': 'artist 1',
            'album': 'album 1',
            'format': 'mp3',
        }
        values.update(kwargs)
        ext = values.pop('format').lower()
        item = Item.from_path(os.path.join(self.fixture_dir, 'min.' + ext))
        item.add(self.lib)
        item.update(values)
        item.move(copy=True)
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
        }
        values.update(kwargs)

        item = Item.from_path(os.path.join(self.fixture_dir, 'min.mp3'))
        item.add(self.lib)
        item.update(values)
        item.move(copy=True)
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


class ThreadPoolMockExecutor(object):

    @classmethod
    def patch(cls):
        target = futures.ThreadPoolExecutor
        cls._orig = {}
        for a in ['__init__', 'submit', 'shutdown']:
            cls._orig[a] = getattr(target, a)
            setattr(target, a, getattr(cls, a).__func__)

    @classmethod
    def unpatch(cls):
        target = futures.ThreadPoolExecutor
        for a, v in cls._orig.items():
            setattr(target, a, v)

    def __init__(self, max_workers=None):
        pass

    def submit(self, fn, *args, **kwargs):
        fut = futures.Future()
        res = fn(*args, **kwargs)
        fut.set_result(res)
        # try:
        #     res = fn(*args, **kwargs)
        # except Exception as e:
        #     fut.set_exception(e)
        # else:
        #     fut.set_result(res)
        return fut

    def shutdown(wait=True):
        pass
