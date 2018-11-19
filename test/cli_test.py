import os
import os.path
import shutil

from helper import TestHelper, control_stdin

from beets.mediafile import MediaFile
from beets import util
from beets.util import bytestring_path, syspath


class DocTest(TestHelper):

    def test_external(self):
        external_dir = os.path.join(self.mkdtemp(), 'myplayer')
        self.config['convert']['formats'] = {
            'aac': {
                'command': 'bash -c "cp \'$source\' \'$dest\';' +
                           'printf ISAAC >> \'$dest\'"',
                'extension': 'm4a'
            },
        }
        self.config['alternatives'] = {
            'myplayer': {
                'directory': external_dir,
                'paths': {'default': u'$artist/$title'},
                'formats': u'aac mp3',
                'query': u'onplayer:true',
                'removable': True,
            }
        }

        self.add_album(artist='Bach', title='was mp3', format='mp3')
        self.add_album(artist='Bach', title='was m4a', format='m4a')
        self.add_album(artist='Bach', title='was ogg', format='ogg')
        self.add_album(artist='Beethoven', title='was ogg', format='ogg')

        external_from_mp3 = bytestring_path(
                os.path.join(external_dir, 'Bach', 'was mp3.mp3'))
        external_from_m4a = bytestring_path(
                os.path.join(external_dir, 'Bach', 'was m4a.m4a'))
        external_from_ogg = bytestring_path(
                os.path.join(external_dir, 'Bach', 'was ogg.m4a'))
        external_beet = bytestring_path(
                os.path.join(external_dir, 'Beethoven', 'was ogg.m4a'))

        self.runcli('modify', '--yes', 'onplayer=true', 'artist:Bach')
        with control_stdin('y'):
            out = self.runcli('alt', 'update', 'myplayer')
            self.assertIn('Do you want to create the collection?', out)

        self.assertNotFileTag(external_from_mp3, b'ISAAC')
        self.assertNotFileTag(external_from_m4a, b'ISAAC')
        self.assertFileTag(external_from_ogg, b'ISAAC')
        self.assertFalse(os.path.isfile(external_beet))

        self.runcli('modify', '--yes', 'composer=JSB', 'artist:Bach')
        self.runcli('alt', 'update', 'myplayer')
        mediafile = MediaFile(syspath(external_from_ogg))
        self.assertEqual(mediafile.composer, 'JSB')

        self.runcli('modify', '--yes', 'onplayer!', 'artist:Bach')
        self.runcli('modify', '--album', '--yes',
                    'onplayer=true', 'albumartist:Beethoven')
        self.runcli('alt', 'update', 'myplayer')

        self.assertFalse(os.path.isfile(external_from_mp3))
        self.assertFalse(os.path.isfile(external_from_m4a))
        self.assertFalse(os.path.isfile(external_from_ogg))
        self.assertFileTag(external_beet, b'ISAAC')

    def test_symlink_view(self):
        self.set_paths_config({
            'default': '$artist/$album/$title'
        })
        self.config['alternatives'] = {
            'by-year': {
                'paths': {'default': '$year/$album/$title'},
                'formats': 'link',
            }
        }

        self.add_album(artist='Michael Jackson', album='Thriller', year='1982')

        self.runcli('alt', 'update', 'by-year')

        self.assertSymlink(
            self.lib_path(b'by-year/1982/Thriller/track 1.mp3'),
            self.lib_path(b'Michael Jackson/Thriller/track 1.mp3'),
        )


class ExternalCopyTest(TestHelper):

    def setUp(self):
        super(ExternalCopyTest, self).setUp()
        external_dir = self.mkdtemp()
        self.config['alternatives'] = {
            'myexternal': {
                'directory': external_dir,
                'query': u'myexternal:true',
            }
        }
        self.external_config = self.config['alternatives']['myexternal']

    def test_add_singleton(self):
        item = self.add_track(title=u'\u00e9', myexternal='true')
        self.runcli('alt', 'update', 'myexternal')
        item.load()
        self.assertIsFile(self.get_path(item))

    def test_add_album(self):
        album = self.add_album()
        album['myexternal'] = 'true'
        album.store()
        self.runcli('alt', 'update', 'myexternal')
        for item in album.items():
            self.assertIsFile(self.get_path(item))

    def test_add_nonexistent(self):
        item = self.add_external_track('myexternal')
        path = self.get_path(item)
        util.remove(path)

        self.runcli('alt', 'update', 'myexternal')
        self.assertIsFile(self.get_path(item))

    def test_add_replace(self):
        item = self.add_external_track('myexternal')
        del item['alt.myexternal']
        item.store()

        self.runcli('alt', 'update', 'myexternal')
        item.load()
        self.assertIn('alt.myexternal', item)

    def test_update_older(self):
        item = self.add_external_track('myexternal')
        item['composer'] = 'JSB'
        item.store()
        item.write()

        self.runcli('alt', 'update', 'myexternal')
        item.load()
        mediafile = MediaFile(syspath(self.get_path(item)))
        self.assertEqual(mediafile.composer, 'JSB')

    def test_no_udpdate_newer(self):
        item = self.add_external_track('myexternal')
        item['composer'] = 'JSB'
        item.store()
        # We omit write to keep old mtime

        self.runcli('alt', 'update', 'myexternal')
        item.load()
        mediafile = MediaFile(syspath(self.get_path(item)))
        self.assertNotEqual(mediafile.composer, 'JSB')

    def test_move_after_path_format_update(self):
        item = self.add_external_track('myexternal')
        old_path = self.get_path(item)
        self.assertIsFile(old_path)

        self.external_config['paths'] = {'default': '$album/$title'}
        self.runcli('alt', 'update', 'myexternal')

        item.load()
        new_path = self.get_path(item)
        self.assertIsNotFile(old_path)
        self.assertIsFile(new_path)

    def test_move_and_write_after_tags_changed(self):
        item = self.add_external_track('myexternal')
        old_path = self.get_path(item)
        self.assertIsFile(old_path)

        item['title'] = 'a new title'
        item.store()
        item.try_write()  # Required to update mtime.
        self.runcli('alt', 'update', 'myexternal')

        item.load()
        new_path = self.get_path(item)
        self.assertIsNotFile(old_path)
        self.assertIsFile(new_path)
        mediafile = MediaFile(syspath(new_path))
        self.assertEqual(mediafile.title, 'a new title')

    def test_prune_after_move(self):
        item = self.add_external_track('myexternal')
        artist_dir = os.path.dirname(self.get_path(item))
        self.assertTrue(os.path.isdir(artist_dir))

        item['artist'] = 'a new artist'
        item.store()
        self.runcli('alt', 'update', 'myexternal')

        self.assertFalse(os.path.exists(syspath(artist_dir)))

    def test_remove_item(self):
        item = self.add_external_track('myexternal')
        old_path = self.get_path(item)
        self.assertIsFile(old_path)

        del item['myexternal']
        item.store()
        self.runcli('alt', 'update', 'myexternal')

        item.load()
        self.assertNotIn('alt.myexternal', item)
        self.assertIsNotFile(old_path)

    def test_remove_album(self):
        album = self.add_external_album('myexternal')
        item = album.items().get()
        old_path = self.get_path(item)
        self.assertIsFile(old_path)

        del album['myexternal']
        album.store()
        self.runcli('alt', 'update', 'myexternal')

        item.load()
        self.assertNotIn('alt.myexternal', item)
        self.assertIsNotFile(old_path)

    def test_unkown_collection(self):
        out = self.runcli('alt', 'update', 'unkown')
        self.assertIn("Alternative collection 'unkown' not found.", out)

    def test_embed_art(self):
        """ Test that artwork is embedded and updated to match the source file.

        There used to be a bug that meant that albumart was only embedded
        once on initial addition to the alternative collection, but not if
        the artwork was added or changed later.

        This test comprehensively checks that embedded artwork is up-to-date
        with the artwork file, even if no changes to the database happen.
        """
        def touch_art(item, image_path):
            """ `touch` the image file, but don't set mtime to the current
            time since the tests run rather fast and item and art mtimes might
            end up identical if the filesystem has low mtime granularity or
            mtimes are cashed as laid out in
                https://stackoverflow.com/a/14393315/3451198
            Considering the interpreter startup time when running `beet alt
            update <name>` in a real use-case, this should not obscure any
            bugs.
            """
            item_mtime_alt = os.path.getmtime(syspath(item.path))
            os.utime(syspath(image_path),
                     (item_mtime_alt + 2, item_mtime_alt + 2)
                     )

        # Initially add album without artwork.
        album = self.add_album(myexternal='true')
        album.store()
        self.runcli('alt', 'update', 'myexternal')

        item = album.items().get()
        self.assertHasNoEmbeddedArtwork(self.get_path(item))

        # Make a copy of the artwork, so that changing mtime/content won't
        # affect the repository.
        image_dir = bytestring_path(self.mkdtemp())
        image_path = os.path.join(image_dir, b'image')
        shutil.copy(self.IMAGE_FIXTURE1, syspath(image_path))
        touch_art(item, image_path)

        # Add a cover image, assert that it is being embedded.
        album.artpath = image_path
        album.store()
        self.runcli('alt', 'update', 'myexternal')

        item = album.items().get()
        self.assertHasEmbeddedArtwork(self.get_path(item), self.IMAGE_FIXTURE1)

        # Change content and update mtime, but do not change the item/album in
        # database.
        # Assert that artwork is re-embedded.
        shutil.copy(self.IMAGE_FIXTURE2, image_path)
        touch_art(item, image_path)
        self.runcli('alt', 'update', 'myexternal')

        item = album.items().get()
        self.assertHasEmbeddedArtwork(self.get_path(item), self.IMAGE_FIXTURE2)


class ExternalConvertTest(TestHelper):

    def setUp(self):
        super(ExternalConvertTest, self).setUp()
        external_dir = self.mkdtemp()
        self.config['convert']['formats'] = {
            'ogg': 'bash -c "cp \'$source\' \'$dest\';' +
                   'printf ISOGG >> \'$dest\'"'
        }
        self.config['alternatives'] = {
            'myexternal': {
                'directory': external_dir,
                'query': u'myexternal:true',
                'formats': 'ogg mp3'
            }
        }
        self.external_config = self.config['alternatives']['myexternal']

    def test_convert(self):
        item = self.add_track(myexternal='true', format='mp4')
        self.runcli('alt', 'update', 'myexternal')
        item.load()
        converted_path = self.get_path(item)
        self.assertFileTag(converted_path, b'ISOGG')

    def test_convert_and_embed(self):
        self.config['convert']['embed'] = True

        album = self.add_album(myexternal='true', format='m4a')
        album.artpath = self.IMAGE_FIXTURE1
        album.store()

        self.runcli('alt', 'update', 'myexternal')
        item = album.items().get()
        self.assertHasEmbeddedArtwork(self.get_path(item))

    def test_convert_write_tags(self):
        item = self.add_track(myexternal='true', format='mp4', title=u'TITLE')

        # We "convert" by copying the file. Setting the title simulates
        # a badly behaved converter
        mediafile_converted = MediaFile(syspath(item.path))
        mediafile_converted.title = u'WRONG'
        mediafile_converted.save()

        self.runcli('alt', 'update', 'myexternal')
        item.load()

        alt_mediafile = MediaFile(syspath(self.get_path(item)))
        self.assertEqual(alt_mediafile.title, u'TITLE')

    def test_skip_convert_for_same_format(self):
        item = self.add_track(myexternal='true')
        item['format'] = 'OGG'
        item.store()
        self.runcli('alt', 'update', 'myexternal')
        item.load()
        converted_path = self.get_path(item)
        self.assertNotFileTag(converted_path, b'ISOGG')

    def test_skip_convert_for_alternative_format(self):
        item = self.add_track(myexternal='true')
        item['format'] = 'MP3'
        item.store()
        self.runcli('alt', 'update', 'myexternal')
        item.load()
        converted_path = self.get_path(item)
        self.assertNotFileTag(converted_path, b'ISOGG')


class ExternalRemovableTest(TestHelper):

    def setUp(self):
        super(ExternalRemovableTest, self).setUp()
        external_dir = os.path.join(self.mkdtemp(), u'\u00e9xt')
        self.config['alternatives'] = {
            'myexternal': {
                'directory': external_dir,
                'query': u'',
            }
        }
        self.external_config = self.config['alternatives']['myexternal']

    def test_ask_create_yes(self):
        item = self.add_track()
        with control_stdin('y'):
            out = self.runcli('alt', 'update', 'myexternal')
            self.assertIn('Do you want to create the collection?', out)
        item.load()
        self.assertIn('alt.myexternal', item)

    def test_ask_create_no(self):
        item = self.add_track()
        with control_stdin('n'):
            out = self.runcli('alt', 'update', 'myexternal')
            self.assertIn('Skipping creation of', out)
        item.load()
        self.assertNotIn('alt.myexternal', item)

    def test_create_option(self):
        item = self.add_track()
        self.runcli('alt', 'update', '--create', 'myexternal')
        item.load()
        self.assertIn('alt.myexternal', item)

    def test_no_create_option(self):
        item = self.add_track()
        self.runcli('alt', 'update', '--no-create', 'myexternal')
        item.load()
        self.assertNotIn('alt.myexternal', item)

    def test_not_removable(self):
        item = self.add_track()
        self.external_config['removable'] = False
        with control_stdin('y'):
            out = self.runcli('alt', 'update', 'myexternal')
            self.assertNotIn('Do you want to create the collection?', out)
        item.load()
        self.assertIn('alt.myexternal', item)
