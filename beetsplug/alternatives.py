# Copyright (c) 2014 Thomas Scholtes

# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

import argparse
import logging
import os.path
import queue
import shutil
from collections.abc import Callable, Iterator, Sequence
from concurrent import futures
from enum import Enum
from pathlib import Path
from typing import Literal, TypeVar

import beets
import beets.plugins
import beetsplug.convert as convert
import confuse
from beets import art, util
from beets.library import Album, Item, Library, parse_query_string
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, UserError, get_path_formats, input_yn, print_
from beets.util.artresizer import ArtResizer
from typing_extensions import Never, override


class AlternativesPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()

    def commands(self):  # pyright: ignore[reportIncompatibleMethodOverride]
        return [AlternativesCommand(self)]

    def update(self, lib: Library, options: argparse.Namespace):
        if options.name is None:
            if not options.all:
                raise UserError("Please specify a collection name or the --all flag")

            for name in self.config.keys():  # noqa: SIM118
                self.alternative(name, lib).update(create=options.create)
        else:
            try:
                alt = self.alternative(options.name, lib)
            except KeyError as e:
                raise UserError(
                    f"Alternative collection '{e.args[0]}' not found."
                ) from e
            alt.update(create=options.create)

    def list_tracks(self, lib: Library, options: argparse.Namespace):
        if options.format is not None:
            beets.config[Item._format_config_key].set(options.format)  # pyright: ignore[reportPrivateUsage]

        alt = self.alternative(options.name, lib)

        # This is slow but we cannot use a native SQL query since the
        # path key is a flexible attribute
        for item in lib.items():
            if alt.path_key in item:
                print_(format(item))

    def alternative(self, name: str, lib: Library):
        config_raw = self.config[name]
        if not config_raw.exists():
            raise KeyError(name)

        config = Config(name, config_raw, lib)

        if config.type == "link":
            return SymlinkView(self._log, lib, config)
        elif config.formats:
            return ExternalConvert(self._log, lib, config)
        else:
            return External(self._log, lib, config)


class AlternativesCommand(Subcommand):
    name = "alt"
    help = "manage alternative files"

    def __init__(self, plugin: AlternativesPlugin):
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

        super().__init__(self.name, parser, self.help)

    def func(self, lib: Library, opts: argparse.Namespace, _):  # pyright: ignore[reportIncompatibleMethodOverride]
        opts.func(lib, opts)

    def parse_args(self, args: list[str]):  # pyright: ignore
        return self.parser.parse_args(args), []


class ArgumentParser(argparse.ArgumentParser):
    """
    Facade for ``argparse.ArgumentParser`` so that beets can call
    `_get_all_options()` to generate shell completion.
    """

    def _get_all_options(self) -> Sequence[Never]:
        # FIXME return options like ``OptionParser._get_all_options``.
        return []


class Config:
    collection_id: str

    type: Literal["copy_convert"] | Literal["link"]
    """Determines whether item files are copied and/or converted or symlinked"""

    directory: Path
    """Directory under which items in the collection are located."""

    path_formats: Sequence[tuple[str, str]]
    """Formats that determine the path of items in the collection. See
    <https://beets.readthedocs.io/en/stable/reference/pathformat.html>.
    """

    formats: Sequence[str]
    """List of acceptable formats for the collection. If an item’s format is not
    in this list the item is transcoded to the first format in the list."""

    removable: bool
    """If true, the user is asked to confirm root directory creation."""

    album_art_embed: bool
    """Embed album art in converted items. Default: yes."""

    album_art_copy: bool
    """Copy or symlink album art to collection"""

    album_art_maxwidth: int | None
    """Maximum width of embedded album art. Larger art is resized."""

    album_art_format: str | None
    """If enabled forced album art to be converted to specified format for the collection. Most often, this will be either JPEG or PNG."""

    album_art_deinterlace: bool
    """If enabled, Pillow or ImageMagick backends are instructed to store cover art as non-progressive JPEG. 
    You might need this if you use DAPs that don’t support progressive images. Default: no."""

    album_art_quality: int
    """JPEG Quality for album art if it is resized. Default: 0"""

    def __init__(self, collection_id: str, config: confuse.ConfigView, lib: Library):
        self.collection_id = collection_id

        if "formats" in config:
            fmt = config["formats"].as_str()
            assert isinstance(fmt, str)
            if fmt == "link":
                self.type = "link"
            else:
                self.type = "copy_convert"
                self.formats = tuple(f.lower() for f in fmt.split())
        else:
            self.type = "copy_convert"
            self.formats = ()

        if "paths" in config:
            path_config = config["paths"]
        else:
            path_config = beets.config["paths"]
        self.path_formats = get_path_formats(path_config)

        query = config["query"].get(confuse.Optional(confuse.String(), default=""))
        self.query, _ = parse_query_string(query, Item)

        removable = config["removable"].get(confuse.TypeTemplate(bool, default=True))
        assert isinstance(removable, bool)
        self.removable = removable

        album_art_embed = config["album_art_embed"].get(
            confuse.TypeTemplate(bool, default=True)
        )
        assert isinstance(album_art_embed, bool)
        self.album_art_embed = album_art_embed

        album_art_copy = config["album_art_copy"].get(
            confuse.TypeTemplate(bool, default=False)
        )
        assert isinstance(album_art_copy, bool)
        self.album_art_copy = album_art_copy

        album_art_maxwidth = config["album_art_maxwidth"].get(
            confuse.Optional(confuse.Integer())
        )
        assert album_art_maxwidth is None or isinstance(album_art_maxwidth, int)
        self.album_art_maxwidth = album_art_maxwidth

        album_art_format = config["album_art_format"].get(
            confuse.Optional(confuse.String())
        )
        assert album_art_format is None or isinstance(album_art_format, str)
        self.album_art_format = album_art_format

        album_art_deinterlace = config["album_art_deinterlace"].get(
            confuse.TypeTemplate(bool, default=False)
        )
        assert isinstance(album_art_deinterlace, bool)
        self.album_art_deinterlace = album_art_deinterlace

        album_art_quality = config["album_art_quality"].get(
            confuse.TypeTemplate(int, default=0)
        )
        assert isinstance(album_art_quality, int)
        self.album_art_quality = album_art_quality

        if "directory" in config:
            dir = config["directory"].as_path()
            assert isinstance(dir, Path)
        else:
            dir = Path(collection_id)
        if not dir.is_absolute():
            dir = Path(str(lib.directory, "utf8")) / dir
        self.directory = dir

        link_type = config["link_type"].get(
            confuse.Choice(
                {
                    "relative": SymlinkType.RELATIVE,
                    "absolute": SymlinkType.ABSOLUTE,
                },
                default=SymlinkType.ABSOLUTE,
            )
        )
        assert isinstance(link_type, SymlinkType)
        self.link_type = link_type


class Action(Enum):
    """Action to take for a track when syncing a collection"""

    #: Track was not present in the collection before and is added
    ADD = "ADD"

    #: Remove the track from the collection
    REMOVE = "REMOVE"

    #: Write tags
    WRITE = "WRITE"

    #: Move the file for an existing track in a collection to a different path.
    MOVE = "MOVE"

    #: Write album art to the track’s metadata
    SYNC_ART = "SYNC_ART"


class External:
    def __init__(self, log: logging.Logger, lib: Library, config: Config):
        self._log = log
        self._config = config
        self.lib = lib
        self.path_key = f"alt.{config.collection_id}"
        self.max_workers = int(str(beets.config["convert"]["threads"]))

    def item_change_actions(
        self, item: Item, actual: Path, dest: Path
    ) -> Sequence[Action]:
        """Returns the necessary actions for items that were previously in the
        external collection, but might require metadata updates.
        """
        actions = []

        if actual != dest:
            actions.append(Action.MOVE)

        item_mtime_alt = actual.stat().st_mtime
        if item_mtime_alt < Path(str(item.path, "utf8")).stat().st_mtime:
            actions.append(Action.WRITE)
        album = item.get_album()

        if (
            self._config.album_art_embed
            and album
            and album.artpath
            and Path(str(album.artpath, "utf8")).is_file()
            and (item_mtime_alt < Path(str(album.artpath, "utf8")).stat().st_mtime)
        ):
            actions.append(Action.SYNC_ART)

        return actions

    def _matched_item_action(self, item: Item) -> Sequence[Action]:
        actual = self._get_stored_path(item)
        if actual and (actual.is_file() or actual.is_symlink()):
            dest = self.destination(item)
            if actual.suffix == dest.suffix:
                return self.item_change_actions(item, actual, dest)
            else:
                # formats config option changed
                return [Action.REMOVE, Action.ADD]
        else:
            return [Action.ADD]

    def _items_actions(self) -> Iterator[tuple[Item, Sequence[Action]]]:
        matched_ids = set()
        for album in self.lib.albums():
            if self._config.query.match(album):
                matched_items = album.items()
                matched_ids.update(item.id for item in matched_items)

        for item in self.lib.items():
            if item.id in matched_ids or self._config.query.match(item):
                yield (item, self._matched_item_action(item))
            elif self._get_stored_path(item):
                yield (item, [Action.REMOVE])

    def ask_create(self, create: bool | None = None) -> bool:
        if not self._config.removable:
            return True
        if create is not None:
            return create

        msg = (
            f"Collection at '{self._config.directory}' does not exists. "
            "Maybe you forgot to mount it.\n"
            "Do you want to create the collection? (y/n)"
        )
        return input_yn(msg, require=True)

    def update(self, create: bool | None = None):  # noqa: C901
        if not self._config.directory.is_dir() and not self.ask_create(create):
            print_(f"Skipping creation of {self._config.directory}")
            return

        def finalize_converted_item(item: Item, dest: Path):
            # Don't rely on the converter to write correct/complete tags.
            item.write(path=bytes(dest))
            if self._config.album_art_embed:
                self._sync_art(item, dest)
            self._set_stored_path(item, dest)
            item.store()
            _send_item_updated(
                collection=self._config.collection_id,
                path=dest,
                item=item,
                action=action,
            )

        converter, converting_done = self._converter()
        with converter as converter:
            for item, actions in self._items_actions():
                dest = self.destination(item)
                path = self._get_stored_path(item)
                for action in actions:
                    delay_finalize = False
                    if action == Action.MOVE:
                        assert (
                            path is not None
                        )  # action guarantees that `path` is not none
                        print_(f">{path} -> {dest}")
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        path.rename(dest)
                        # beets types are confusing
                        util.prune_dirs(
                            str(path.parent), root=str(self._config.directory)
                        )  # pyright: ignore
                        self._set_stored_path(item, dest)
                        item.store()
                        path = dest
                    elif action == Action.WRITE:
                        assert (
                            path is not None
                        )  # action guarantees that `path` is not none
                        print_(f"*{path}")
                        item.write(path=bytes(path))
                    elif action == Action.SYNC_ART:
                        print_(f"~{path}")
                        assert path is not None
                        self._sync_art(item, path)
                    elif action == Action.ADD:
                        print_(f"+{dest}")
                        dest.parent.mkdir(exist_ok=True, parents=True)
                        if self._should_transcode(item):
                            delay_finalize = True
                            converter.run(item, dest)
                        else:
                            self._log.debug(f"copying {dest}")
                            shutil.copyfile(item.path, dest)
                            if self._config.album_art_embed:
                                self._sync_art(item, dest)
                            self._set_stored_path(item, dest)
                            item.store()

                    elif action == Action.REMOVE:
                        assert (
                            path is not None
                        )  # action guarantees that `path` is not none
                        print_(f"-{path}")
                        self._remove_file(item)
                        item.store()

                    if not delay_finalize:
                        _send_item_updated(
                            collection=self._config.collection_id,
                            path=dest,
                            item=item,
                            action=action,
                        )

                for item, dest in _get_queue_available(converting_done):
                    finalize_converted_item(item, dest)

            for item, dest in converter.as_completed():
                for item, dest in _get_queue_available(converting_done):
                    finalize_converted_item(item, dest)

            if self._config.album_art_copy:
                self.update_art()

    def update_art(self, link: bool = False):
        for album in self.lib.albums():
            if not self._config.query.match(album) and not any(
                self._config.query.match(item) for item in album.items()
            ):
                continue

            dest_dir = self.album_destination(album)
            if not dest_dir:
                continue

            artpath = album.artpath and Path(str(album.artpath, "utf8"))
            if not artpath or not artpath.is_file():
                continue

            dest = album.art_destination(album.artpath, bytes(dest_dir))
            dest = Path(str(dest, "utf8"))

            if self._config.album_art_format and not link:
                new_format = self._config.album_art_format.lower()
                if new_format == "jpeg":
                    new_format = "jpg"

                dest = dest.with_suffix(f".{new_format}")

            if dest.is_file() and dest.stat().st_mtime >= artpath.stat().st_mtime:
                continue

            artpath = bytes(artpath)

            if link:
                self._log.debug(f"Linking art from {album.artpath} to {dest}")
                util.link(artpath, bytes(dest), replace=True)
            else:
                path = self.resize_art(artpath)
                self._log.debug(f"Copying art from {path} to {dest}")
                util.copy(path, bytes(dest), replace=True)

            print_(f"~{dest}")

    def resize_art(self, path: bytes) -> bytes:
        """Resize the candidate artwork according to the plugin's
        configuration and the specified check.
        """
        if self._config.album_art_maxwidth:
            self._log.debug(f"Resizing {path} to {self._config.album_art_maxwidth}")
            path = ArtResizer.shared.resize(
                self._config.album_art_maxwidth,
                path,
                quality=self._config.album_art_quality,
            )

        format = ArtResizer.shared.get_format(path)
        if self._config.album_art_format and self._config.album_art_format != format:
            self._log.debug(f"Reformatting {path} to {self._config.album_art_format}")
            tmp_path = util.get_temp_filename(__name__, "reformat", path)
            util.copy(path, tmp_path, replace=True)
            path = ArtResizer.shared.reformat(
                tmp_path,
                self._config.album_art_format,
                deinterlaced=self._config.album_art_deinterlace,
            )

        elif self._config.album_art_deinterlace:
            self._log.debug(f"Deinterlacing {path}")
            path = ArtResizer.shared.deinterlace(path)

        return path

    def destination(self, item: Item) -> Path:
        """Returns the path for `item` in the external collection."""
        path = _item_destination_relative_compat(
            item, path_formats=self._config.path_formats
        )
        assert isinstance(path, str)
        return self._config.directory / path

    def album_destination(self, album: Album) -> Path | None:
        items = album.items()
        if len(items) > 0:
            return self.destination(items[0]).parent
        else:
            return None

    def _set_stored_path(self, item: Item, path: Path):
        item[self.path_key] = str(path)

    def _get_stored_path(self, item: Item) -> Path | None:
        try:
            path = item[self.path_key]
        except KeyError:
            return None

        if isinstance(path, str):
            return Path(path)
        else:
            return None

    def _remove_file(self, item: Item):
        """Remove the external file for `item`."""
        path = self._get_stored_path(item)
        if path:
            path.unlink(missing_ok=True)
            # beets types are confusing
            util.prune_dirs(str(path), root=str(self._config.directory))  # pyright: ignore
        del item[self.path_key]

    def _converter(self) -> tuple["Worker", queue.Queue[tuple[Item, Path]]]:
        done_queue = queue.Queue()

        def _convert(item: Item, dest: Path):
            raise RuntimeError(
                "Convert must never be called for non-converting collection"
            )

        return (Worker(_convert, self.max_workers), done_queue)

    def _sync_art(self, item: Item, path: Path):
        """Embed artwork in the file at `path`."""
        album = item.get_album()
        if album and album.artpath and Path(str(album.artpath, "utf8")).is_file():
            self._log.debug(f"Embedding art from {album.artpath} into {path}")

            artpath = self.resize_art(album.artpath)

            art.embed_item(
                self._log,
                item,
                artpath,
                maxwidth=self._config.album_art_maxwidth,
                itempath=bytes(path),
            )

    def _should_transcode(self, item: Item) -> bool:
        return False


class ExternalConvert(External):
    def __init__(
        self,
        log: logging.Logger,
        lib: Library,
        config: Config,
    ):
        super().__init__(log, lib, config)
        convert_plugin = convert.ConvertPlugin()
        self._encode = convert_plugin.encode
        self._formats = [convert.ALIASES.get(f, f) for f in config.formats]
        self.convert_cmd, self.ext = convert.get_format(self._formats[0])

    @override
    def _converter(self) -> tuple["Worker", queue.Queue[tuple[Item, Path]]]:
        done_queue = queue.Queue()

        def _convert(item: Item, dest: Path):
            self._encode(self.convert_cmd, item.path, bytes(dest))
            done_queue.put((item, dest))
            return item, dest

        return Worker(_convert, self.max_workers), done_queue

    @override
    def destination(self, item: Item) -> Path:
        dest = super().destination(item)
        if self._should_transcode(item):
            return dest.with_suffix("." + self.ext.decode("utf8"))
        else:
            return dest

    @override
    def _should_transcode(self, item: Item):
        return item.format.lower() not in self._formats


class SymlinkType(Enum):
    ABSOLUTE = 0
    RELATIVE = 1


class SymlinkView(External):
    @override
    def item_change_actions(
        self, item: Item, actual: Path, dest: Path
    ) -> Sequence[Action]:
        """Returns the necessary actions for items that were previously in the
        external collection, but might require metadata updates.
        """

        if (
            actual == dest
            and actual.is_file()  # Symlink not broken, `.samefile()` doesn’t throw
            and actual.samefile(Path(str(item.path, "utf8")))
        ):
            return []
        else:
            return [Action.MOVE]

    @override
    def update(self, create: bool | None = None):
        for item, actions in self._items_actions():
            dest = self.destination(item)
            path = self._get_stored_path(item)
            for action in actions:
                if action == Action.MOVE:
                    assert path is not None  # action guarantees that `path` is not none
                    print_(f">{path} -> {dest}")
                    self._remove_file(item)
                    self._create_symlink(item)
                    self._set_stored_path(item, dest)
                    item.store()
                elif action == Action.ADD:
                    print_(f"+{dest}")
                    self._create_symlink(item)
                    self._set_stored_path(item, dest)
                    item.store()
                elif action == Action.REMOVE:
                    assert path is not None  # action guarantees that `path` is not none
                    print_(f"-{path}")
                    self._remove_file(item)
                    item.store()

                _send_item_updated(
                    collection=self._config.collection_id,
                    path=dest,
                    item=item,
                    action=action,
                )

        if self._config.album_art_copy:
            self.update_art(link=True)

    def _create_symlink(self, item: Item):
        dest = self.destination(item)
        dest.parent.mkdir(exist_ok=True, parents=True)
        item_path = Path(str(item.path, "utf8"))
        link = (
            os.path.relpath(item_path, dest.parent)
            if self._config.link_type == SymlinkType.RELATIVE
            else item_path
        )
        dest.symlink_to(link)

    @override
    def _sync_art(self, item: Item, path: Path):
        pass


class Worker(futures.ThreadPoolExecutor):
    def __init__(
        self, fn: Callable[[Item, Path], tuple[Item, Path]], max_workers: int | None
    ):
        super().__init__(max_workers)
        self._tasks: set[futures.Future[tuple[Item, Path]]] = set()
        self._fn = fn

    def run(self, item: Item, path: Path):
        fut = self.submit(self._fn, item, path)
        self._tasks.add(fut)
        return fut

    def as_completed(self, timeout: float | None = None):
        for f in futures.as_completed(self._tasks, timeout=timeout):
            self._tasks.remove(f)
            yield f.result()


_T = TypeVar("_T")


def _get_queue_available(q: queue.Queue[_T] | queue.SimpleQueue[_T]):
    while True:
        try:
            item = q.get(block=False)
        except queue.Empty:
            break
        yield item


def _send_item_updated(*, collection: str, path: Path, item: Item, action: Action):
    beets.plugins.send(
        "alternatives.item_updated",  # type: ignore Custom event
        collection=collection,
        path=path,
        item=item,
        action=action.value,
    )


_beets_version = tuple(map(int, beets.__version__.split(".")[0:2]))

if _beets_version >= (2, 3):

    def _item_destination_relative_compat(
        item: Item, path_formats: object = None
    ) -> str:
        path_bytes = item.destination(
            path_formats=path_formats, relative_to_libdir=True
        )
        return path_bytes.decode("utf-8")

else:

    def _item_destination_relative_compat(
        item: Item, path_formats: object = None
    ) -> str:
        path = item.destination(path_formats=path_formats, fragment=True)  # type: ignore
        assert isinstance(path, str)
        return path
