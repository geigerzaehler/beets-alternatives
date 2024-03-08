# Copyright (c) 2014 Thomas Scholtes
# -*- coding: utf-8 -*-

# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.


import argparse
import os.path
import threading
import traceback
from concurrent import futures
from enum import Enum
from typing import Iterator, List, Optional, Tuple

import beets
from beets import art, util
from beets.library import Item, Library, parse_query_string
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, UserError, decargs, get_path_formats, input_yn, print_
from beets.util import FilesystemError, bytestring_path, displayable_path, syspath
from typing_extensions import override

import beetsplug.convert as convert


def _remove(path, soft=True):
    """Remove the file. If `soft`, then no error will be raised if the
    file does not exist.
    In contrast to beets' util.remove, this uses lexists such that it can
    actually remove symlink links.
    """
    path = syspath(path)
    if soft and not os.path.lexists(path):
        return
    try:
        os.remove(path)
    except (OSError, IOError) as exc:
        raise FilesystemError(exc, "delete", (path,), traceback.format_exc())


class AlternativesPlugin(BeetsPlugin):
    def __init__(self):
        super(AlternativesPlugin, self).__init__()

    def commands(self):
        return [AlternativesCommand(self)]

    def update(self, lib: Library, options: argparse.Namespace):
        if options.name is None:
            if not options.all:
                raise UserError("Please specify a collection name or the --all flag")

            for name in self.config.keys():
                self.alternative(name, lib).update(create=options.create)
        else:
            try:
                alt = self.alternative(options.name, lib)
            except KeyError as e:
                raise UserError(
                    "Alternative collection '{0}' not found.".format(e.args[0])
                )
            alt.update(create=options.create)

    def list_tracks(self, lib, options):
        if options.format is not None:
            (fmt,) = decargs([options.format])
            beets.config[Item._format_config_key].set(fmt)

        alt = self.alternative(options.name, lib)

        # This is slow but we cannot use a native SQL query since the
        # path key is a flexible attribute
        for item in lib.items():
            if alt.path_key in item:
                print_(format(item))

    def alternative(self, name: str, lib: Library):
        conf = self.config[name]
        if not conf.exists():
            raise KeyError(name)

        if conf["formats"].exists():
            fmt = conf["formats"].as_str()
            assert isinstance(fmt, str)
            if fmt == "link":
                return SymlinkView(self._log, name, lib, conf)
            else:
                return ExternalConvert(self._log, name, fmt.split(), lib, conf)
        else:
            return External(self._log, name, lib, conf)


class AlternativesCommand(Subcommand):
    name = "alt"
    help = "manage alternative files"

    def __init__(self, plugin):
        parser = ArgumentParser()
        subparsers = parser.add_subparsers(prog=parser.prog + " alt")
        subparsers.required = True

        update = subparsers.add_parser("update")
        update.set_defaults(func=plugin.update)
        update.add_argument(
            "name",
            metavar="NAME",
            nargs="?",
            help="Name of the collection. Must be  provided unless --all is given",
        )
        update.add_argument("--create", action="store_const", dest="create", const=True)
        update.add_argument(
            "--no-create", action="store_const", dest="create", const=False
        )
        update.add_argument(
            "--all",
            action="store_true",
            default=False,
            help="Update all alternative collections that are defined in the configuration",
        )

        list_tracks = subparsers.add_parser(
            "list-tracks",
            description="""
                List all tracks that are currently part of an alternative
                collection""",
        )
        list_tracks.set_defaults(func=plugin.list_tracks)
        list_tracks.add_argument(
            "name",
            metavar="NAME",
            help="Name of the alternative",
        )
        list_tracks.add_argument(
            "-f",
            "--format",
            metavar="FORMAT",
            dest="format",
            help="""Format string to print for each track. See beets’
                Path Formats for more information.""",
        )

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


class Action(Enum):
    ADD = 1
    REMOVE = 2
    WRITE = 3
    MOVE = 4
    SYNC_ART = 5


class External(object):
    def __init__(self, log, name, lib, config):
        self._log = log
        self.name = name
        self.lib = lib
        self.path_key = "alt.{0}".format(name)
        self.max_workers = int(str(beets.config["convert"]["threads"]))
        self.parse_config(config)

    def parse_config(self, config):
        if "paths" in config:
            path_config = config["paths"]
        else:
            path_config = beets.config["paths"]
        self.path_formats = get_path_formats(path_config)
        query = config["query"].as_str()
        self.query, _ = parse_query_string(query, Item)

        self.removable = config.get(dict).get("removable", True)

        if "directory" in config:
            dir = config["directory"].as_str()
        else:
            dir = self.name
        dir = bytestring_path(dir)
        if not os.path.isabs(syspath(dir)):
            dir = os.path.join(self.lib.directory, dir)
        self.directory = dir

    def item_change_actions(self, item: Item, path: bytes, dest: bytes) -> List[Action]:
        """Returns the necessary actions for items that were previously in the
        external collection, but might require metadata updates.
        """
        actions = []

        if not util.samefile(path, dest):
            actions.append(Action.MOVE)

        item_mtime_alt = os.path.getmtime(syspath(path))
        if item_mtime_alt < os.path.getmtime(syspath(item.path)):
            actions.append(Action.WRITE)
        album = item.get_album()

        if album:
            if (
                album.artpath
                and os.path.isfile(syspath(album.artpath))
                and (item_mtime_alt < os.path.getmtime(syspath(album.artpath)))
            ):
                actions.append(Action.SYNC_ART)

        return actions

    def _matched_item_action(self, item: Item) -> List[Action]:
        path = self._get_stored_path(item)
        if path and os.path.lexists(syspath(path)):
            dest = self.destination(item)
            _, path_ext = os.path.splitext(path)
            _, dest_ext = os.path.splitext(dest)
            if not path_ext == dest_ext:
                # formats config option changed
                return [Action.REMOVE, Action.ADD]
            else:
                return self.item_change_actions(item, path, dest)
        else:
            return [Action.ADD]

    def _items_actions(self) -> Iterator[Tuple[Item, List[Action]]]:
        matched_ids = set()
        for album in self.lib.albums():
            if self.query.match(album):
                matched_items = album.items()
                matched_ids.update(item.id for item in matched_items)

        for item in self.lib.items():
            if item.id in matched_ids or self.query.match(item):
                yield (item, self._matched_item_action(item))
            elif self._get_stored_path(item):
                yield (item, [Action.REMOVE])

    def ask_create(self, create: Optional[bool] = None) -> bool:
        if not self.removable:
            return True
        if create is not None:
            return create

        msg = (
            "Collection at '{0}' does not exists. "
            "Maybe you forgot to mount it.\n"
            "Do you want to create the collection? (y/n)".format(
                displayable_path(self.directory)
            )
        )
        return input_yn(msg, require=True)

    def update(self, create: Optional[bool] = None):
        if not os.path.isdir(syspath(self.directory)) and not self.ask_create(create):
            print_("Skipping creation of {0}".format(displayable_path(self.directory)))
            return

        converter = self._converter()
        for item, actions in self._items_actions():
            dest = self.destination(item)
            path = self._get_stored_path(item)
            for action in actions:
                if action == Action.MOVE:
                    print_(
                        ">{0} -> {1}".format(
                            displayable_path(path), displayable_path(dest)
                        )
                    )
                    util.mkdirall(dest)
                    util.move(path, dest)
                    assert path is not None
                    util.prune_dirs(os.path.dirname(path), root=self.directory)
                    self._set_stored_path(item, dest)
                    item.store()
                    path = dest
                elif action == Action.WRITE:
                    print_("*{0}".format(displayable_path(path)))
                    item.write(path=path)
                elif action == Action.SYNC_ART:
                    print_("~{0}".format(displayable_path(path)))
                    assert path is not None
                    self._sync_art(item, path)
                elif action == Action.ADD:
                    print_("+{0}".format(displayable_path(dest)))
                    converter.submit(item)
                elif action == Action.REMOVE:
                    print_("-{0}".format(displayable_path(path)))
                    self._remove_file(item)
                    item.store()

        for item, dest in converter.as_completed():
            self._set_stored_path(item, dest)
            item.store()
        converter.shutdown()

    def destination(self, item: Item) -> bytes:
        """Returns the path for `item` in the external collection."""
        path = item.destination(basedir=self.directory, path_formats=self.path_formats)
        assert isinstance(path, bytes)
        return path

    def _set_stored_path(self, item: Item, path: bytes):
        item[self.path_key] = str(path, "utf8")

    def _get_stored_path(self, item: Item) -> Optional[bytes]:
        try:
            path = item[self.path_key]
        except KeyError:
            return None
        if path:
            return path.encode("utf8")
        else:
            return None

    def _remove_file(self, item: Item):
        """Remove the external file for `item`."""
        path = self._get_stored_path(item)
        _remove(path)
        util.prune_dirs(path, root=self.directory)
        del item[self.path_key]

    def _converter(self) -> "Worker":
        def _convert(item):
            dest = self.destination(item)
            util.mkdirall(dest)
            util.copy(item.path, dest, replace=True)
            return item, dest

        return Worker(_convert, self.max_workers)

    def _sync_art(self, item: Item, path: bytes):
        """Embed artwork in the file at `path`."""
        album = item.get_album()
        if album:
            if album.artpath and os.path.isfile(syspath(album.artpath)):
                self._log.debug(
                    "Embedding art from {} into {}".format(
                        displayable_path(album.artpath), displayable_path(path)
                    )
                )
                art.embed_item(self._log, item, album.artpath, itempath=path)


class ExternalConvert(External):
    def __init__(self, log, name, formats, lib, config):
        super(ExternalConvert, self).__init__(log, name, lib, config)
        convert_plugin = convert.ConvertPlugin()
        self._encode = convert_plugin.encode
        self._embed = convert_plugin.config["embed"].get(bool)
        formats = [f.lower() for f in formats]
        self.formats = [convert.ALIASES.get(f, f) for f in formats]
        self.convert_cmd, self.ext = convert.get_format(self.formats[0])

    @override
    def _converter(self) -> "Worker":
        fs_lock = threading.Lock()

        def _convert(item):
            dest = self.destination(item)
            with fs_lock:
                util.mkdirall(dest)

            if self._should_transcode(item):
                self._encode(self.convert_cmd, item.path, dest)
                # Don't rely on the converter to write correct/complete tags.
                item.write(path=dest)
            else:
                self._log.debug("copying {0}".format(displayable_path(dest)))
                util.copy(item.path, dest, replace=True)
            if self._embed:
                self._sync_art(item, dest)
            return item, dest

        return Worker(_convert, self.max_workers)

    @override
    def destination(self, item: Item) -> bytes:
        dest = super().destination(item)
        if self._should_transcode(item):
            return os.path.splitext(dest)[0] + b"." + self.ext
        else:
            return dest

    def _should_transcode(self, item):
        return item.format.lower() not in self.formats


class SymlinkType(Enum):
    ABSOLUTE = 0
    RELATIVE = 1


class SymlinkView(External):
    @override
    def parse_config(self, config):
        if "query" not in config:
            config["query"] = ""  # This is a TrueQuery()
        if "link_type" not in config:
            # Default as absolute so it doesn't break previous implementation
            config["link_type"] = "absolute"

        self.relativelinks = config["link_type"].as_choice(
            {"relative": SymlinkType.RELATIVE, "absolute": SymlinkType.ABSOLUTE}
        )

        super(SymlinkView, self).parse_config(config)

    @override
    def item_change_actions(self, item: Item, path: bytes, dest: bytes):
        """Returns the necessary actions for items that were previously in the
        external collection, but might require metadata updates.
        """
        actions = []

        if not path == dest:
            # The path of the link itself changed
            actions.append(Action.MOVE)
        elif not util.samefile(path, item.path):
            # link target changed
            actions.append(Action.MOVE)

        return actions

    @override
    def update(self, create=None):
        for item, actions in self._items_actions():
            dest = self.destination(item)
            path = self._get_stored_path(item)
            for action in actions:
                if action == Action.MOVE:
                    print_(
                        ">{0} -> {1}".format(
                            displayable_path(path), displayable_path(dest)
                        )
                    )
                    self._remove_file(item)
                    self._create_symlink(item)
                    self._set_stored_path(item, dest)
                elif action == Action.ADD:
                    print_("+{0}".format(displayable_path(dest)))
                    self._create_symlink(item)
                    self._set_stored_path(item, dest)
                elif action == Action.REMOVE:
                    print_("-{0}".format(displayable_path(path)))
                    self._remove_file(item)
                else:
                    continue
                item.store()

    def _create_symlink(self, item: Item):
        dest = self.destination(item)
        util.mkdirall(dest)
        link = (
            os.path.relpath(item.path, os.path.dirname(dest))
            if self.relativelinks == SymlinkType.RELATIVE
            else item.path
        )
        util.link(link, dest)

    @override
    def _sync_art(self, item: Item, path: bytes):
        pass


class Worker(futures.ThreadPoolExecutor):
    def __init__(self, fn, max_workers: Optional[int]):
        super(Worker, self).__init__(max_workers)
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
