# Copyright (c) 2014 Thomas Scholtes

# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.


import os.path
import logging
import threading
from argparse import ArgumentParser
from concurrent import futures

from beets import util
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, get_path_formats
from beets.library import get_query_sort, Item
from beets.util import syspath

from beetsplug import convert

log = logging.getLogger('beets.alternatives')


class AlternativesPlugin(BeetsPlugin):

    def __init__(self):
        super(AlternativesPlugin, self).__init__()

    def commands(self):
        return [AlternativesCommand(self)]

    def update(self, lib, options):
        self.alternative(options.name, lib).update()

    def alternative(self, name, lib):
        conf = self.config['external'][name]
        if conf.exists():
            if conf['format'].exists():
                return ExternalConvert(name, conf['format'].get(unicode),
                                       lib, conf)
            else:
                return External(name, lib, conf)


class AlternativesCommand(Subcommand):

    name = 'alt'
    help = 'manage alternative files'

    def __init__(self, plugin):
        parser = ArgumentParser()
        subparsers = parser.add_subparsers()
        update = subparsers.add_parser('update')
        update.set_defaults(func=plugin.update)
        update.add_argument('name')
        super(AlternativesCommand, self).__init__(self.name, parser)

    def func(self, lib, opts, _):
        opts.func(lib, opts)

    def parse_args(self, args):
        return self.parser.parse_args(args), []


class External(object):

    ADD = 1
    REMOVE = 2
    WRITE = 3
    MOVE = 4

    def __init__(self, name, lib, config):
        self.lib = lib
        self.path_key = 'alt.{0}'.format(name)
        self.path_formats = get_path_formats(config['paths'])
        self.query, _ = get_query_sort(config['query'].get(unicode), Item)

        dir = config['directory'].as_filename()
        if not os.path.isabs(dir):
            dir = os.path.join(lib.directory, dir)
        self.directory = dir

    def items_action(self, items):
        for item in items:
            path = item.get(self.path_key)
            if self.query.match(item):
                if path:
                    dest = self.destination(item)
                    if path != dest:
                        yield (item, self.MOVE)
                    elif (os.path.getmtime(syspath(dest))
                          < os.path.getmtime(syspath(item.path))):
                        yield (item, self.WRITE)
                else:
                    yield (item, self.ADD)
            elif path:
                yield (item, self.REMOVE)

    def update(self):
        converter = self.converter()
        for (item, action) in self.items_action(self.lib.items()):
            dest = self.destination(item)
            path = item.get(self.path_key)
            if action == self.MOVE:
                print('>{0} -> {1}'.format(path, dest))
                util.mkdirall(dest)
                util.move(path, dest)
                item[self.path_key] = dest
                item.store()
            elif action == self.WRITE:
                print('*{0}'.format(path))
                item.write(path=path)
            elif action == self.ADD:
                print('+{0}'.format(dest))
                converter.submit(item)
            elif action == self.REMOVE:
                print('-{0}'.format(self.destination(item)))
                util.remove(path)
                util.prune_dirs(path)
                del item[self.path_key]
                item.store()

        for item, dest in converter.as_completed():
            item[self.path_key] = dest
            item.store()
        converter.shutdown()

    def destination(self, item):
        return item.destination(basedir=self.directory,
                                path_formats=self.path_formats)

    def converter(self):
        def _convert(item):
            dest = self.destination(item)
            util.mkdirall(dest)
            util.copy(item.path, dest)
            return item, dest
        return Worker(_convert)


class ExternalConvert(External):

    def __init__(self, name, format, lib, config):
        super(ExternalConvert, self).__init__(name, lib, config)
        self.format = format
        self.convert_cmd, self.ext = convert.get_format(self.format)

    def converter(self):
        command, ext = convert.get_format(self.format)
        fs_lock = threading.Lock()

        def _convert(item):
            dest = self.destination(item)
            with fs_lock:
                util.mkdirall(dest)

            if not self.format or self.format.lower() == item.format.lower():
                util.copy(item.path, dest)
            else:
                convert.encode(command, item.path, dest)
            return item, dest
        return Worker(_convert)

    def destination(self, item):
        dest = super(ExternalConvert, self).destination(item)
        return os.path.splitext(dest)[0] + '.' + self.ext


class Worker(futures.ThreadPoolExecutor):

    def __init__(self, fn, max_workers=None):
        super(Worker, self).__init__(max_workers=1)
        self._tasks = set()
        self._fn = fn

    def submit(self, *args, **kwargs):
        fut = super(Worker, self).submit(self._fn, *args, **kwargs)
        self._tasks.add(fut)
        return fut

    def as_completed(self):
        for f in futures.as_completed(self._tasks):
            self._tasks.remove(f)
            yield f.result()
