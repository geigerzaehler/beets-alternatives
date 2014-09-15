import os
import os.path
from unittest import TestCase

from helper import TestHelper, control_stdin

from beets.mediafile import MediaFile


class DocTest(TestHelper, TestCase):

    def test_external(self):
        external_dir = os.path.join(self.mkdtemp(), 'myplayer')
        self.config['convert']['formats'] = {
            'aac': {
                'command': 'cp $source $dest; printf ISAAC >> $dest',
                'extension': 'm4a'
            },
        }
        self.config['alternatives'] = {
            'myplayer': {
                'directory': external_dir,
                'paths': {'default': '$artist/$title'},
                'formats': 'aac mp3',
                'query': 'onplayer:true',
                'removable': True,
            }
        }

        self.add_album(artist='Bach', title='was mp3', format='mp3')
        self.add_album(artist='Bach', title='was m4a', format='m4a')
        self.add_album(artist='Bach', title='was ogg', format='ogg')
        self.add_album(artist='Beethoven', title='was ogg', format='ogg')

        external_from_mp3 = os.path.join(external_dir, 'Bach', 'was mp3.mp3')
        external_from_m4a = os.path.join(external_dir, 'Bach', 'was m4a.m4a')
        external_from_ogg = os.path.join(external_dir, 'Bach', 'was ogg.m4a')
        external_beet = os.path.join(external_dir, 'Beethoven', 'was ogg.m4a')

        self.runcli('modify', '--yes', 'onplayer=true', 'artist:Bach')
        with control_stdin('y'):
            out = self.runcli('alt', 'update', 'myplayer')
            self.assertIn('Do you want to create the collection?', out)

        self.assertNotFileTag(external_from_mp3, 'ISAAC')
        self.assertNotFileTag(external_from_m4a, 'ISAAC')
        self.assertFileTag(external_from_ogg, 'ISAAC')
        self.assertFalse(os.path.isfile(external_beet))

        self.runcli('modify', '--yes', 'composer=JSB', 'artist:Bach')
        self.runcli('alt', 'update', 'myplayer')
        mediafile = MediaFile(external_from_ogg)
        self.assertEqual(mediafile.composer, 'JSB')

        self.runcli('modify', '--yes', 'onplayer!', 'artist:Bach')
        self.runcli('modify', '--yes', 'onplayer=true', 'artist:Beethoven')
        self.runcli('alt', 'update', 'myplayer')

        self.assertFalse(os.path.isfile(external_from_mp3))
        self.assertFalse(os.path.isfile(external_from_m4a))
        self.assertFalse(os.path.isfile(external_from_ogg))
        self.assertFileTag(external_beet, 'ISAAC')

    def test_symlink_view(self):
        self.set_paths_config({
            'default': '$artist/$album/$title'
        })
        self.config['alternatives'] = {
            'by-year': {
                'directory': 'by-year',
                'paths': {'default': '$year/$album/$title'},
                'formats': 'link',
            }
        }

        self.add_album(artist='Michael Jackson', album='Thriller', year='1982')

        self.runcli('alt', 'update', 'by-year')

        self.assertSymlink(
            self.lib_path('by-year/1982/Thriller/track 1.mp3'),
            self.lib_path('Michael Jackson/Thriller/track 1.mp3'),
        )


class ExternalCopyTest(TestHelper, TestCase):

    def setUp(self):
        super(ExternalCopyTest, self).setUp()
        self.external_dir = self.mkdtemp()
        self.config['alternatives'] = {
            'myexternal': {
                'directory': self.external_dir,
                'query': 'myexternal:true',
            }
        }
        self.external_config = self.config['alternatives']['myexternal']

    def test_add(self):
        item = self.add_track(title=u'\u00e9', myexternal='true')
        self.runcli('alt', 'update', 'myexternal')
        item.load()
        self.assertTrue(os.path.isfile(item['alt.myexternal']))

    def test_update_older(self):
        item = self.add_external_track('myexternal')
        item['composer'] = 'JSB'
        item.store()
        item.write()

        self.runcli('alt', 'update', 'myexternal')
        item.load()
        mediafile = MediaFile(item['alt.myexternal'])
        self.assertEqual(mediafile.composer, 'JSB')

    def test_no_udpdate_newer(self):
        item = self.add_external_track('myexternal')
        item['composer'] = 'JSB'
        item.store()
        # We omit write to keep old mtime

        self.runcli('alt', 'update', 'myexternal')
        item.load()
        mediafile = MediaFile(item['alt.myexternal'])
        self.assertNotEqual(mediafile.composer, 'JSB')

    def test_move_after_path_format_update(self):
        item = self.add_external_track('myexternal')
        old_path = item['alt.myexternal']
        self.assertTrue(os.path.isfile(old_path))

        self.external_config['paths'] = {'default': '$album/$title'}
        self.runcli('alt', 'update', 'myexternal')

        item.load()
        new_path = item['alt.myexternal']
        self.assertFalse(os.path.isfile(old_path))
        self.assertTrue(os.path.isfile(new_path))

    def test_move_after_tags_changed(self):
        item = self.add_external_track('myexternal')
        old_path = item['alt.myexternal']
        self.assertTrue(os.path.isfile(old_path))

        item['title'] = 'a new title'
        item.store()
        self.runcli('alt', 'update', 'myexternal')

        item.load()
        new_path = item['alt.myexternal']
        self.assertFalse(os.path.isfile(old_path))
        self.assertTrue(os.path.isfile(new_path))

    def test_remove(self):
        item = self.add_external_track('myexternal')
        old_path = item['alt.myexternal']
        self.assertTrue(os.path.isfile(old_path))

        del item['myexternal']
        item.store()
        self.runcli('alt', 'update', 'myexternal')

        item.load()
        self.assertNotIn('alt.myexternal', item)
        self.assertFalse(os.path.isfile(old_path))

    def test_unkown_collection(self):
        out = self.runcli('alt', 'update', 'unkown')
        self.assertIn("Alternative collection 'unkown' not found.", out)


class ExternalConvertTest(TestHelper, TestCase):

    def setUp(self):
        super(ExternalConvertTest, self).setUp()
        self.external_dir = self.mkdtemp()
        self.config['convert']['formats'] = {
            'ogg': 'cp $source $dest; printf ISOGG >> $dest'
        }
        self.config['alternatives'] = {
            'myexternal': {
                'directory': self.external_dir,
                'query': 'myexternal:true',
                'formats': 'ogg mp3'
            }
        }
        self.external_config = self.config['alternatives']['myexternal']

    def test_convert(self):
        item = self.add_track(myexternal='true', format='mp4')
        self.runcli('alt', 'update', 'myexternal')
        item.load()
        converted_path = item['alt.myexternal']
        self.assertFileTag(converted_path, 'ISOGG')

    def test_skip_convert_for_same_format(self):
        item = self.add_track(myexternal='true')
        item['format'] = 'OGG'
        item.store()
        self.runcli('alt', 'update', 'myexternal')
        item.load()
        converted_path = item['alt.myexternal']
        self.assertNotFileTag(converted_path, 'ISOGG')

    def test_skip_convert_for_alternative_format(self):
        item = self.add_track(myexternal='true')
        item['format'] = 'MP3'
        item.store()
        self.runcli('alt', 'update', 'myexternal')
        item.load()
        converted_path = item['alt.myexternal']
        self.assertNotFileTag(converted_path, 'ISOGG')


class ExternalRemovableTest(TestHelper, TestCase):

    def setUp(self):
        super(ExternalRemovableTest, self).setUp()
        external_dir = os.path.join(self.mkdtemp(), u'\u00e9xt')
        self.config['alternatives'] = {
            'myexternal': {
                'directory': external_dir,
                'query': '',
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
