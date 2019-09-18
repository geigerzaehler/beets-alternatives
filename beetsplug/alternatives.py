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
import threading
import argparse
from concurrent import futures
import six

import beets
from beets import util, art
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, get_path_formats, input_yn, UserError, print_
from beets.library import parse_query_string, Item
from beets.util import syspath, displayable_path, cpu_count, bytestring_path

from beetsplug import convert


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
            fmt = conf['formats'].as_str()
            if fmt == u'link':
                return SymlinkView(self._log, name, lib, conf)
            else:
                return ExternalConvert(self._log, name, fmt.split(), lib, conf)
        else:
            return External(self._log, name, lib, conf)


class AlternativesCommand(Subcommand):

    name = 'alt'
    help = 'manage alternative files'

    def __init__(self, plugin):
        parser = ArgumentParser()
        subparsers = parser.add_subparsers(prog=parser.prog + ' alt')
        subparsers.required = True
        subparsers.dest = 'update'
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


class ArgumentParser(argparse.ArgumentParser):
    """
    Facade for ``argparse.ArgumentParser`` so that beets can call
    `_get_all_options()` to generate shell completion.
    """
    def _get_all_options(self):
        # FIXME return options like ``OptionParser._get_all_options``.
        return []


class External(object):

    ADD = 1
    REMOVE = 2
    WRITE = 3
    MOVE = 4
    SYNC_ART = 5

    def __init__(self, log, name, lib, config):
        self._log = log
        self.name = name
        self.lib = lib
        self.path_key = u'alt.{0}'.format(name)
        self.parse_config(config)

    def parse_config(self, config):
        if 'paths' in config:
            path_config = config['paths']
        else:
            path_config = beets.config['paths']
        self.path_formats = get_path_formats(path_config)
        query = config['query'].as_str()
        self.query, _ = parse_query_string(query, Item)

        self.removable = config.get(dict).get('removable', True)

        if 'directory' in config:
            dir = config['directory'].as_str()
        else:
            dir = self.name
        dir = bytestring_path(dir)
        if not os.path.isabs(syspath(dir)):
            dir = os.path.join(self.lib.directory, dir)
        self.directory = dir

    def matched_item_action(self, item):
        path = self.get_path(item)
        if path and os.path.isfile(syspath(path)):
            dest = self.destination(item)
            _, path_ext = os.path.splitext(path)
            _, dest_ext = os.path.splitext(dest)
            if not path_ext == dest_ext:
                # formats config option changed
                return (item, [self.REMOVE, self.ADD])
            actions = []
            if not util.samefile(path, dest):
                actions.append(self.MOVE)
            item_mtime_alt = os.path.getmtime(syspath(path))
            if (item_mtime_alt < os.path.getmtime(syspath(item.path))):
                actions.append(self.WRITE)
            album = item.get_album()
            if album:
                if (album.artpath and
                        os.path.isfile(syspath(album.artpath)) and
                        (item_mtime_alt
                         < os.path.getmtime(syspath(album.artpath)))):
                    actions.append(self.SYNC_ART)
            return (item, actions)
        else:
            return (item, [self.ADD])

    def items_actions(self):
        matched_ids = set()
        for album in self.lib.albums():
            if self.query.match(album):
                matched_items = album.items()
                matched_ids.update(item.id for item in matched_items)

        for item in self.lib.items():
            if item.id in matched_ids or self.query.match(item):
                yield self.matched_item_action(item)
            elif self.get_path(item):
                yield (item, [self.REMOVE])

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
        if (not os.path.isdir(syspath(self.directory))
                and not self.ask_create(create)):
            print_(u'Skipping creation of {0}'
                   .format(displayable_path(self.directory)))
            return

        converter = self.converter()
        for (item, actions) in self.items_actions():
            dest = self.destination(item)
            path = self.get_path(item)
            for action in actions:
                if action == self.MOVE:
                    print_(u'>{0} -> {1}'.format(displayable_path(path),
                                                 displayable_path(dest)))
                    util.mkdirall(dest)
                    util.move(path, dest)
                    util.prune_dirs(os.path.dirname(path), root=self.directory)
                    self.set_path(item, dest)
                    item.store()
                    path = dest
                elif action == self.WRITE:
                    print_(u'*{0}'.format(displayable_path(path)))
                    item.write(path=path)
                elif action == self.SYNC_ART:
                    print_(u'~{0}'.format(displayable_path(path)))
                    self.sync_art(item, path)
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
        item[self.path_key] = six.text_type(path, 'utf8')

    @staticmethod
    def _get_path(item, path_key):
        try:
            return item[path_key].encode('utf8')
        except KeyError:
            return None

    def get_path(self, item):
        return self._get_path(item, self.path_key)

    def remove_item(self, item):
        path = self.get_path(item)
        util.remove(path)
        util.prune_dirs(path, root=self.directory)
        del item[self.path_key]

    def converter(self):
        def _convert(item):
            dest = self.destination(item)
            util.mkdirall(dest)
            util.copy(item.path, dest, replace=True)
            return item, dest
        return Worker(_convert)

    def sync_art(self, item, path):
        """ Embed artwork in the destination file.
        """
        album = item.get_album()
        if album:
            if album.artpath and os.path.isfile(syspath(album.artpath)):
                self._log.debug("Embedding art from {} into {}".format(
                                displayable_path(album.artpath),
                                displayable_path(path)))
                art.embed_item(self._log, item, album.artpath,
                               itempath=path)


class ExternalConvert(External):

    def __init__(self, log, name, formats, lib, config):
        super(ExternalConvert, self).__init__(log, name, lib, config)
        convert_plugin = convert.ConvertPlugin()
        self._encode = convert_plugin.encode
        self._embed = convert_plugin.config['embed'].get(bool)
        formats = [f.lower() for f in formats]
        self.formats = [convert.ALIASES.get(f, f) for f in formats]
        self.convert_cmd, self.ext = convert.get_format(self.formats[0])

    def converter(self):
        fs_lock = threading.Lock()

        def _convert(item):
            dest = self.destination(item)
            with fs_lock:
                util.mkdirall(dest)

            if self.should_transcode(item):
                self._encode(self.convert_cmd, item.path, dest)
                # Don't rely on the converter to write correct/complete tags.
                item.write(path=dest)
            else:
                self._log.debug(u'copying {0}'.format(displayable_path(dest)))
                util.copy(item.path, dest, replace=True)
            if self._embed:
                self.sync_art(item, dest)
            return item, dest
        return Worker(_convert)

    def destination(self, item):
        dest = super(ExternalConvert, self).destination(item)
        if self.should_transcode(item):
            return os.path.splitext(dest)[0] + b'.' + self.ext
        else:
            return dest

    def should_transcode(self, item):
        return item.format.lower() not in self.formats


class SymlinkView(External):
    LINK_ABSOLUTE = 0
    LINK_RELATIVE = 1

    def parse_config(self, config):
        if 'query' not in config:
            config['query'] = u''  # This is a TrueQuery()
        if 'link_type' not in config:
            # Default as absolute so it doesn't break previous implementation
            config['link_type'] = 'absolute'

        self.relativelinks = config['link_type'].as_choice(
            {"relative": self.LINK_RELATIVE, "absolute": self.LINK_ABSOLUTE})

        super(SymlinkView, self).parse_config(config)

    def update(self, create=None):
        for (item, actions) in self.items_actions():
            dest = self.destination(item)
            path = self.get_path(item)
            for action in actions:
                if action == self.MOVE:
                    print_(u'>{0} -> {1}'.format(displayable_path(path),
                                                 displayable_path(dest)))
                    self.remove_item(item)
                    self.create_symlink(item)
                    self.set_path(item, dest)
                elif action == self.ADD:
                    print_(u'+{0}'.format(displayable_path(dest)))
                    self.create_symlink(item)
                    self.set_path(item, dest)
                elif action == self.REMOVE:
                    print_(u'-{0}'.format(displayable_path(path)))
                    self.remove_item(item)
                else:
                    continue
                item.store()

    def create_symlink(self, item):
        dest = self.destination(item)
        util.mkdirall(dest)
        link = (
            os.path.relpath(item.path, os.path.dirname(dest))
            if self.relativelinks == self.LINK_RELATIVE else item.path)
        util.link(link, dest)


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
