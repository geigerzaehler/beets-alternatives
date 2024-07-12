import os
import os.path
import platform
import shutil
from pathlib import Path
from time import sleep

import pytest
from beets.library import Item
from beets.ui import UserError
from confuse import ConfigValueError
from mediafile import MediaFile

from .helper import (
    TestHelper,
    assert_file_tag,
    assert_has_embedded_artwork,
    assert_has_not_embedded_artwork,
    assert_is_not_file,
    assert_media_file_fields,
    assert_not_file_tag,
    assert_symlink,
    control_stdin,
    convert_command,
)


class TestDoc(TestHelper):
    """Test alternatives in a larger-scale scenario with transcoding and
    multiple changes to the library.
    """

    def test_external(self, tmp_path: Path):
        external_dir = tmp_path / "myplayer"
        self.config["convert"]["formats"] = {
            "aac": {
                "command": convert_command("ISAAC"),
                "extension": "m4a",
            },
        }
        self.config["alternatives"] = {
            "myplayer": {
                "directory": str(external_dir),
                "paths": {"default": "$artist/$title"},
                "formats": "aac mp3",
                "query": "onplayer:true",
                "removable": True,
            }
        }

        self.add_album(artist="Bach", title="was mp3", format="mp3")
        self.add_album(artist="Bach", title="was m4a", format="m4a")
        self.add_album(artist="Bach", title="was ogg", format="ogg")
        self.add_album(artist="Beethoven", title="was ogg", format="ogg")

        external_from_mp3 = external_dir / "Bach" / "was mp3.mp3"
        external_from_m4a = external_dir / "Bach" / "was m4a.m4a"
        external_from_ogg = external_dir / "Bach" / "was ogg.m4a"
        external_beet = external_dir / "Beethoven" / "was ogg.m4a"

        self.runcli("modify", "--yes", "onplayer=true", "artist:Bach")
        with control_stdin("y"):
            out = self.runcli("alt", "update", "myplayer")
            assert "Do you want to create the collection?" in out

        assert_not_file_tag(external_from_mp3, b"ISAAC")
        assert_not_file_tag(external_from_m4a, b"ISAAC")
        assert_file_tag(external_from_ogg, b"ISAAC")
        assert not external_beet.exists()

        self.runcli("modify", "--yes", "composer=JSB", "artist:Bach")

        list_output = self.runcli(
            "alt", "list-tracks", "myplayer", "--format", "$artist $title"
        )
        assert list_output == "\n".join([
            "Bach was mp3",
            "Bach was m4a",
            "Bach was ogg",
            "",
        ])

        self.runcli("alt", "update", "myplayer")
        mediafile = MediaFile(external_from_ogg)
        assert mediafile.composer == "JSB"

        self.runcli("modify", "--yes", "onplayer!", "artist:Bach")
        self.runcli(
            "modify", "--album", "--yes", "onplayer=true", "albumartist:Beethoven"
        )
        self.runcli("alt", "update", "myplayer")

        list_output = self.runcli(
            "alt", "list-tracks", "myplayer", "--format", "$artist"
        )
        assert list_output == "Beethoven\n"

        assert not external_from_mp3.exists()
        assert not external_from_m4a.exists()
        assert not external_from_ogg.exists()
        assert_file_tag(external_beet, b"ISAAC")


@pytest.mark.skipif(platform.system() == "Windows", reason="no symlinks on windows")
class TestSymlinkView(TestHelper):
    """Test alternatives with the ``link`` format producing symbolic links."""

    @pytest.fixture(autouse=True)
    def _symlink_view(self):
        self.lib.path_formats = (("default", "$artist/$album/$title"),)
        self.config["alternatives"] = {
            "by-year": {
                "paths": {"default": "$year/$album/$title"},
                "formats": "link",
            }
        }
        self.alt_config = self.config["alternatives"]["by-year"]

    def _test_add_move_remove_album(self, *, absolute: bool):
        """Test that symlinks are created, moved and deleted."""

        self.add_album(
            artist="Michael Jackson",
            album="Thriller",
            year="1990",
            original_year="1982",
        )

        # Symlink is created
        self.runcli("alt", "update", "by-year")
        alt_path = self.libdir / "by-year/1990/Thriller/track 1.mp3"
        library_path = self.libdir / "Michael Jackson/Thriller/track 1.mp3"
        assert_symlink(alt_path, library_path, absolute)

        # Alternative is not updated
        assert self.runcli("alt", "update", "by-year") == ""

        # Symlink location is updated when path config changes
        self.alt_config["paths"]["default"] = "$original_year/$album/$title"
        self.runcli("alt", "update", "by-year")
        alt_path = self.libdir / "by-year/1982/Thriller/track 1.mp3"
        assert_symlink(alt_path, library_path, absolute)

        # Symlink is removed
        self.alt_config["query"] = "some_field::foobar"
        self.runcli("alt", "update", "by-year")
        assert_is_not_file(alt_path)

    def test_add_move_remove_album_absolute(self):
        """Test that absolute symlinks are created, moved and deleted."""

        self.alt_config["link_type"] = "absolute"
        self._test_add_move_remove_album(absolute=True)

    def test_add_move_remove_album_relative(self):
        """Test that relative symlinks are created, moved and deleted."""

        self.alt_config["link_type"] = "relative"
        self._test_add_move_remove_album(absolute=False)

    def test_update_link_target(self, tmp_path: Path):
        """Link targets are updated when the item has moved in the library"""

        self.add_album(artist="Michael Jackson", album="Thriller", year="1990")

        self.runcli("alt", "update", "by-year")

        alt_path = self.libdir / "by-year/1990/Thriller/track 1.mp3"
        assert_symlink(
            link=alt_path,
            target=self.libdir / "Michael Jackson/Thriller/track 1.mp3",
            absolute=True,
        )

        # Moving a library item breaks the symlink
        new_libdir = tmp_path / "newlib"
        new_libdir.mkdir()
        self.runcli("move", "-a", "-d", str(new_libdir), "Thriller")
        assert alt_path.is_symlink()
        assert not alt_path.is_file()

        # Updating the alternative fixes the symlink
        self.runcli("alt", "update", "by-year")
        assert_symlink(
            link=alt_path,
            target=new_libdir / "Michael Jackson/Thriller/track 1.mp3",
            absolute=True,
        )

    def test_invalid_link_type(self):
        self.alt_config["link_type"] = "Hylian"

        with pytest.raises(ConfigValueError):
            self.runcli("alt", "update", "by-year")


class TestExternalCopy(TestHelper):
    """Test alternatives with empty ``format `` option, i.e. only copying
    without transcoding.
    """

    @pytest.fixture(autouse=True)
    def _external_copy(self, tmp_path: Path, _setup: None):
        self.config["alternatives"] = {
            "myexternal": {
                "directory": str(tmp_path),
                "query": "myexternal:true",
            }
        }
        self.external_config = self.config["alternatives"]["myexternal"]

    def test_add_singleton(self):
        item = self.add_track(title="\u00e9", myexternal="true")
        self.runcli("alt", "update", "myexternal")
        item.load()
        assert self.get_path(item).is_file()

    def test_add_album(self):
        album = self.add_album()
        album["myexternal"] = "true"
        album.store()
        self.runcli("alt", "update", "myexternal")
        for item in album.items():
            assert self.get_path(item).is_file()

    def test_add_nonexistent(self):
        item = self.add_external_track("myexternal")
        path = self.get_path(item)
        path.unlink()

        self.runcli("alt", "update", "myexternal")
        assert path.is_file()

    def test_add_replace(self):
        item = self.add_external_track("myexternal")
        del item["alt.myexternal"]
        item.store()

        self.runcli("alt", "update", "myexternal")
        item.load()
        assert "alt.myexternal" in item

    def test_update_older(self):
        item = self.add_external_track("myexternal")
        sleep(0.1)
        item["composer"] = "JSB"
        item.store()
        item.write()

        self.runcli("alt", "update", "myexternal")
        item.load()
        mediafile = MediaFile(self.get_path(item))
        assert mediafile.composer == "JSB"

    def test_no_update_newer(self):
        item = self.add_external_track("myexternal")
        sleep(0.1)
        item["composer"] = "JSB"
        item.store()
        # We omit write to keep old mtime

        self.runcli("alt", "update", "myexternal")
        item.load()
        mediafile = MediaFile(self.get_path(item))
        assert mediafile.composer != "JSB"

    def test_move_after_path_format_update(self):
        item = self.add_external_track("myexternal")
        old_path = self.get_path(item)
        assert old_path.is_file()

        self.external_config["paths"] = {"default": "$album/$title"}
        self.runcli("alt", "update", "myexternal")

        item.load()
        new_path = self.get_path(item)
        assert_is_not_file(old_path)
        assert new_path.is_file()

    def test_move_and_write_after_tags_changed(self):
        item = self.add_external_track("myexternal")
        old_path = self.get_path(item)
        assert old_path.is_file()

        sleep(0.1)
        item["title"] = "a new title"
        item.store()
        item.write()
        self.runcli("alt", "update", "myexternal")

        item.load()
        new_path = self.get_path(item)
        assert_is_not_file(old_path)
        assert new_path.is_file()
        mediafile = MediaFile(new_path)
        assert mediafile.title == "a new title"

    def test_prune_after_move(self):
        item = self.add_external_track("myexternal")
        item_alt_path = self.get_path(item)
        assert item_alt_path
        assert item_alt_path.parent.is_dir()

        item["artist"] = "a new artist"
        item.store()
        self.runcli("alt", "update", "myexternal")

        assert not item_alt_path.parent.is_dir()

    def test_remove_item(self):
        item = self.add_external_track("myexternal")
        old_path = self.get_path(item)
        assert old_path.is_file()

        del item["myexternal"]
        item.store()
        self.runcli("alt", "update", "myexternal")

        item.load()
        assert "alt.myexternal" not in item
        assert_is_not_file(old_path)

    def test_remove_album(self):
        album = self.add_external_album("myexternal")
        item = album.items().get()
        old_path = self.get_path(item)
        assert old_path.is_file()

        del album["myexternal"]
        album.store()
        self.runcli("alt", "update", "myexternal")

        item.load()
        assert "alt.myexternal" not in item
        assert_is_not_file(old_path)

    def test_unkown_collection(self):
        with pytest.raises(UserError) as e:
            self.runcli("alt", "update", "unkown")
        assert str(e.value) == "Alternative collection 'unkown' not found."

    def test_embed_art(self, tmp_path: Path):
        """Test that artwork is embedded and updated to match the source file.

        There used to be a bug that meant that albumart was only embedded
        once on initial addition to the alternative collection, but not if
        the artwork was added or changed later.

        This test comprehensively checks that embedded artwork is up-to-date
        with the artwork file, even if no changes to the database happen.
        """

        def touch_art(item: Item, image_path: Path):
            """`touch` the image file, but don't set mtime to the current
            time since the tests run rather fast and item and art mtimes might
            end up identical if the filesystem has low mtime granularity or
            mtimes are cashed as laid out in
                https://stackoverflow.com/a/14393315/3451198
            Considering the interpreter startup time when running `beet alt
            update <name>` in a real use-case, this should not obscure any
            bugs.
            """
            item_mtime_alt = Path(str(item.path, "utf8")).stat().st_mtime
            os.utime(image_path, (item_mtime_alt + 2, item_mtime_alt + 2))

        # Initially add album without artwork.
        album = self.add_album(myexternal="true")
        album.store()
        self.runcli("alt", "update", "myexternal")

        item = album.items().get()
        assert_has_not_embedded_artwork(self.get_path(item))

        # Make a copy of the artwork, so that changing mtime/content won't
        # affect the repository.
        image_path = tmp_path / "image"
        shutil.copy(self.IMAGE_FIXTURE1, image_path)
        touch_art(item, image_path)

        # Add a cover image, assert that it is being embedded.
        album.artpath = bytes(image_path)
        album.store()
        self.runcli("alt", "update", "myexternal")

        item = album.items().get()
        assert_has_embedded_artwork(self.get_path(item), self.IMAGE_FIXTURE1)

        # Change content and update mtime, but do not change the item/album in
        # database.
        # Assert that artwork is re-embedded.
        shutil.copy(self.IMAGE_FIXTURE2, image_path)
        touch_art(item, image_path)
        self.runcli("alt", "update", "myexternal")

        item = album.items().get()
        assert_has_embedded_artwork(self.get_path(item), self.IMAGE_FIXTURE2)

    def test_update_all(self, tmp_path: Path):
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()
        self.config["alternatives"].get().clear()  # type: ignore
        self.config["alternatives"] = {
            "a": {
                "directory": str(dir_a),
                "query": "myexternal:true",
            },
            "b": {
                "directory": str(dir_b),
                "query": "myexternal:true",
            },
        }

        with pytest.raises(UserError) as e:
            self.runcli("alt", "update")
        assert str(e.value) == "Please specify a collection name or the --all flag"

        item = self.add_track(title="a", myexternal="true")
        self.runcli("alt", "update", "--all")
        item.load()
        path_a = self.get_path(item, path_key="alt.a")
        assert path_a
        assert dir_a in path_a.parents
        assert path_a.is_file()

        path_b = self.get_path(item, path_key="alt.b")
        assert path_b
        assert dir_b in path_b.parents
        assert path_b.is_file()

        # Don’t update files on second run
        assert self.runcli("alt", "update", "--all") == ""

    def test_update_subset_query(self):
        """When a query is provided via the command line only update items in the
        collection that also match that query."""

        item_add = self.add_track(title="add", myexternal="true", foo="true")
        item_dont_add = self.add_track(title="don't add", myexternal="true", foo="")
        # Ensure that we don’t inadvertently add items that match the CLI query
        # but not the collection query.
        item_not_in_collection = self.add_track(title="not in collection", foo="true")

        self.runcli("alt", "update", "myexternal", "--query", "foo:true")

        item_add.load()
        assert self.get_path(item_add).is_file()

        item_dont_add.load()
        assert not self.get_path(item_dont_add).is_file()

        item_not_in_collection.load()
        with pytest.raises(KeyError):
            self.get_path(item_not_in_collection)


class TestExternalConvert(TestHelper):
    """Test alternatives with non-empty ``format`` option, i.e. transcoding
    some of the files.
    """

    @pytest.fixture(autouse=True)
    def _external_convert(self, tmp_path: Path, _setup: None):
        external_dir = str(tmp_path)
        self.config["convert"]["formats"] = {"ogg": convert_command("ISOGG")}
        self.config["alternatives"] = {
            "myexternal": {
                "directory": external_dir,
                "query": "myexternal:true",
                "formats": "ogg mp3",
            }
        }
        self.external_config = self.config["alternatives"]["myexternal"]

    def test_convert(self):
        item = self.add_track(myexternal="true", format="m4a")
        self.runcli("alt", "update", "myexternal")
        item.load()
        converted_path = self.get_path(item)
        assert_file_tag(converted_path, b"ISOGG")

    def test_convert_and_embed(self):
        self.config["convert"]["embed"] = True

        album = self.add_album(myexternal="true", format="m4a")
        album.artpath = bytes(self.IMAGE_FIXTURE1)
        album.store()

        self.runcli("alt", "update", "myexternal")
        item = album.items().get()
        assert_has_embedded_artwork(self.get_path(item))

    def test_convert_write_tags(self):
        item = self.add_track(myexternal="true", format="m4a", title="TITLE")

        # We "convert" by copying the file. Setting the title simulates
        # a badly behaved converter
        mediafile_converted = MediaFile(item.path)
        mediafile_converted.title = "WRONG"
        mediafile_converted.save()

        self.runcli("alt", "update", "myexternal")
        item.load()

        alt_mediafile = MediaFile(self.get_path(item))
        assert alt_mediafile.title == "TITLE"

    def test_skip_convert_for_same_format(self):
        item = self.add_track(myexternal="true")
        item["format"] = "OGG"
        item.store()
        self.runcli("alt", "update", "myexternal")
        item.load()
        converted_path = self.get_path(item)
        assert_not_file_tag(converted_path, b"ISOGG")

    def test_skip_convert_for_alternative_format(self):
        item = self.add_track(myexternal="true")
        item["format"] = "MP3"
        item.store()
        self.runcli("alt", "update", "myexternal")
        item.load()
        converted_path = self.get_path(item)
        assert_not_file_tag(converted_path, b"ISOGG")

    def test_no_move_on_extension_change(self):
        item = self.add_track(myexternal="true", format="m4a")
        self.runcli("alt", "update", "myexternal")

        self.config["convert"]["formats"] = {"mp3": convert_command("ISMP3")}
        self.config["alternatives"]["myexternal"]["formats"] = "mp3"

        # Assert that this re-encodes instead of copying the ogg file
        self.runcli("alt", "update", "myexternal")
        item.load()
        converted_path = self.get_path(item)
        assert_file_tag(converted_path, b"ISMP3")


@pytest.mark.skipif(platform.system() == "Windows", reason="converter not implemented")
class TestExternalConvertWorker(TestHelper):
    """Test alternatives with non-empty ``format`` option, i.e. transcoding
    some of the files. In contrast to the previous test, these test do use
    the parallelizing ``beetsplug.alternatives.Worker``.
    """

    def test_convert_multiple(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.undo()
        external_dir = str(tmp_path)
        self.config["convert"]["formats"] = {
            "ogg": "bash -c \"cp '{source}' '$dest'\"".format(
                # The convert plugin will encode this again using arg_encoding
                source=self.item_fixture_path("ogg")
            )
        }
        self.config["alternatives"] = {
            "myexternal": {
                "directory": external_dir,
                "query": "myexternal:true",
                "formats": "ogg",
            }
        }

        items = [
            self.add_track(
                title=f"track {i}",
                myexternal="true",
                format="m4a",
            )
            for i in range(24)
        ]
        self.runcli("alt", "update", "myexternal")
        for item in items:
            item.load()
            converted_path = self.get_path(item)
            assert_media_file_fields(converted_path, type="ogg", title=item.title)


class TestExternalRemovable(TestHelper):
    """Test whether alternatives properly detects ``removable`` collections
    and performs the expected user queries before doing anything.
    """

    @pytest.fixture(autouse=True)
    def _external_removable(self, tmp_path: Path, _setup: None):
        external_dir = str(tmp_path / "\u00e9xt")
        self.config["alternatives"] = {
            "myexternal": {
                "directory": external_dir,
                "query": "",
            }
        }
        self.external_config = self.config["alternatives"]["myexternal"]

    def test_ask_create_yes(self):
        item = self.add_track()
        with control_stdin("y"):
            out = self.runcli("alt", "update", "myexternal")
            assert "Do you want to create the collection?" in out
        item.load()
        assert "alt.myexternal" in item

    def test_ask_create_no(self):
        item = self.add_track()
        with control_stdin("n"):
            out = self.runcli("alt", "update", "myexternal")
            assert "Skipping creation of" in out
        item.load()
        assert "alt.myexternal" not in item

    def test_create_option(self):
        item = self.add_track()
        self.runcli("alt", "update", "--create", "myexternal")
        item.load()
        assert "alt.myexternal" in item

    def test_no_create_option(self):
        item = self.add_track()
        self.runcli("alt", "update", "--no-create", "myexternal")
        item.load()
        assert "alt.myexternal" not in item

    def test_not_removable(self):
        item = self.add_track()
        self.external_config["removable"] = False
        with control_stdin("y"):
            out = self.runcli("alt", "update", "myexternal")
            assert "Do you want to create the collection?" not in out
        item.load()
        assert "alt.myexternal" in item


class TestCompletion(TestHelper):
    """Test invocation of ``beet completion`` with this plugin.

    Only ensures that command does not fail.
    """

    def test_completion(self):
        self.runcli("completion")
