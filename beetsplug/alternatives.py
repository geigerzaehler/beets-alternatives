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

import beets
from beets import util
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, get_path_formats, input_yn, UserError, print_
from beets.library import parse_query_string, Item
from beets.util import syspath, displayable_path, cpu_count, bytestring_path
from beets.dbcore.query import NoneQuery

from beetsplug import convert

log = logging.getLogger('beets.alternatives')


class AlternativesPlugin(BeetsPlugin):

    def __init__(self):
        super(AlternativesPlugin, self).__init__()

    def commands(self):
        return [AlternativesCommand(self)]

    def update(self, lib, options):
        try:
            alt = self.alternative(options.name, lib)
        except KeyError as e:
            raise UserError(u"Alternative collection '{0}' not found."
                            .format(e.args[0]))
        alt.update(create=options.create)

    def alternative(self, name, lib):
        conf = self.config[name]
        if not conf.exists():
            raise KeyError(name)

        if conf['formats'].exists():
            fmt = conf['formats'].get(unicode)
            if fmt == 'link':
                return SymlinkView(name, lib, conf)
            else:
                return ExternalConvert(name, fmt.split(), lib, conf)
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
        update.add_argument('--create', action='store_const',
                            dest='create', const=True)
        update.add_argument('--no-create', action='store_const',
                            dest='create', const=False)
        super(AlternativesCommand, self).__init__(self.name, parser, self.help)

    def func(self, lib, opts, _):
        opts.func(lib, opts)

    def parse_args(self, args):
        return self.parser.parse_args(args), []


class External(object):

    ADD = 1
    REMOVE = 2
    WRITE = 3
    MOVE = 4
    NOOP = 5

    def __init__(self, name, lib, config):
        self.name = name
        self.lib = lib
        self.path_key = 'alt.{0}'.format(name)
        self.parse_config(config)

    def parse_config(self, config):
        if 'paths' in config:
            path_config = config['paths']
        else:
            path_config = beets.config['paths']
        self.path_formats = get_path_formats(path_config)
        self.query, _ = parse_query_string(config['query'].get(unicode), Item)

        self.removable = config.get(dict).get('removable', True)

        dir = config['directory'].get(str)
        if not os.path.isabs(dir):
            dir = os.path.join(self.lib.directory, dir)
        self.directory = bytestring_path(dir)

    def matched_item_action(self, item):
        path = self.get_path(item)
        if path:
            dest = self.destination(item)
            if path != dest:
                return (item, self.MOVE)
            elif (os.path.getmtime(syspath(dest))
                  < os.path.getmtime(syspath(item.path))):
                return (item, self.WRITE)
            else:
                return (item, self.NOOP)
        else:
            return (item, self.ADD)

    def items_action(self):
        for album in self.lib.albums():
            if self.query.match(album):
                matched_items = album.items()
                unmatched_items = []
            else:
                matched_items = [i for i in album.items()
                                 if self.query.match(i)]
                unmatched_items = [i for i in album.items()
                                   if not self.query.match(i)]

            for item in matched_items:
                yield self.matched_item_action(item)
            for item in unmatched_items:
                if self.get_path(item):
                    yield (item, self.REMOVE)

        for item in self.lib.items(NoneQuery('album_id')):
            if self.query.match(item):
                yield self.matched_item_action(item)
            elif self.get_path(item):
                yield (item, self.REMOVE)

    def ask_create(self, create=None):
        if not self.removable:
            return True
        if create is not None:
            return create

        msg = u"Collection at '{0}' does not exists. " \
              "Maybe you forgot to mount it.\n" \
              "Do you want to create the collection? (y/n)" \
              .format(displayable_path(self.directory))
        return input_yn(msg, require=True)

    def update(self, create=None):
        if not os.path.isdir(self.directory) and not self.ask_create(create):
            print_(u'Skipping creation of {0}'
                   .format(displayable_path(self.directory)))
            return

        converter = self.converter()
        for (item, action) in self.items_action():
            dest = self.destination(item)
            path = self.get_path(item)
            if action == self.MOVE:
                print_(u'>{0} -> {1}'.format(displayable_path(path),
                                             displayable_path(dest)))
                util.mkdirall(dest)
                util.move(path, dest)
                util.prune_dirs(path, root=self.directory)
                self.set_path(item, dest)
                item.store()
                item.write(path=dest)
            elif action == self.WRITE:
                print_(u'*{0}'.format(displayable_path(path)))
                item.write(path=path)
            elif action == self.ADD:
                print_(u'+{0}'.format(displayable_path(dest)))
                converter.submit(item)
            elif action == self.REMOVE:
                print_(u'-{0}'.format(displayable_path(path)))
                self.remove_item(item)
                item.store()

        for item, dest in converter.as_completed():
            self.set_path(item, dest)
            item.store()
        converter.shutdown()

    def destination(self, item):
        return item.destination(basedir=self.directory,
                                path_formats=self.path_formats)

    def set_path(self, item, path):
        item[self.path_key] = unicode(path, 'utf8')

    def get_path(self, item):
        try:
            return item[self.path_key].encode('utf8')
        except KeyError:
            return None

    def remove_item(self, item):
        path = item[self.path_key].encode('utf8')
        util.remove(path)
        util.prune_dirs(path, root=self.directory)
        del item[self.path_key]

    def converter(self):
        def _convert(item):
            dest = self.destination(item)
            util.mkdirall(dest)
            util.copy(item.path, dest)
            return item, dest
        return Worker(_convert)


class ExternalConvert(External):

    def __init__(self, name, formats, lib, config):
        super(ExternalConvert, self).__init__(name, lib, config)
        self.formats = [f.lower() for f in formats]
        self.convert_cmd, self.ext = convert.get_format(self.formats[0])

    def converter(self):
        fs_lock = threading.Lock()

        def _convert(item):
            dest = self.destination(item)
            with fs_lock:
                util.mkdirall(dest)

            if self.should_transcode(item):
                convert.encode(self.convert_cmd, item.path, dest)
            else:
                log.debug(u'copying {0}'.format(displayable_path(dest)))
                util.copy(item.path, dest)
            return item, dest
        return Worker(_convert)

    def destination(self, item):
        dest = super(ExternalConvert, self).destination(item)
        if self.should_transcode(item):
            return os.path.splitext(dest)[0] + '.' + self.ext
        else:
            return dest

    def should_transcode(self, item):
        return item.format.lower() not in self.formats


class SymlinkView(External):

    def parse_config(self, config):
        if 'query' not in config:
            config['query'] = ''  # This is a TrueQuery()
        super(SymlinkView, self).parse_config(config)

    def update(self, create=None):
        for (item, action) in self.items_action():
            dest = self.destination(item)
            path = self.get_path(item)
            if action == self.MOVE:
                print_(u'>{0} -> {1}'.format(displayable_path(path),
                                             displayable_path(dest)))
                self.remove_item(item)
                self.create_symlink(item)
                self.set_path(item, dest)
                item.store()
            elif action == self.ADD:
                print_(u'+{0}'.format(displayable_path(dest)))
                self.create_symlink(item)
                self.set_path(item, dest)
                item.store()
            elif action == self.REMOVE:
                print_(u'-{0}'.format(displayable_path(path)))
                self.remove_item(item)
            else:
                continue
            item.store()

    def create_symlink(self, item):
        dest = self.destination(item)
        util.mkdirall(dest)
        os.symlink(item.path, dest)


class Worker(futures.ThreadPoolExecutor):

    def __init__(self, fn, max_workers=None):
        super(Worker, self).__init__(max_workers or cpu_count())
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
