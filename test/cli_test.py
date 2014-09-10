import os
import os.path
from unittest import TestCase

from helper import TestHelper, control_stdin

from beets.mediafile import MediaFile


class DocTest(TestHelper, TestCase):

    def setUp(self):
        super(DocTest, self).setUp()
        self.add_album(artist='Bach')
        self.add_album(artist='Beethoven')

    def test_external(self):
        external_dir = os.path.join(self.mkdtemp(), 'myplayer')
        self.config['convert']['formats'] = {
            'ogg': 'cp $source $dest; printf ISOGG >> $dest'
        }
        self.config['alternatives']['external'] = {
            'myplayer': {
                'directory': external_dir,
                'paths': {'default': '$artist/$title'},
                'format': 'ogg',
                'query': 'onplayer:true',
                'removable': True,
            }
        }

        external_bach = os.path.join(external_dir, 'Bach', 'track 1.ogg')
        external_beet = os.path.join(external_dir, 'Beethoven', 'track 1.ogg')

        self.runcli('modify', '--yes', 'onplayer=true', 'artist:Bach')
        with control_stdin('y'):
            out = self.runcli('alt', 'update', 'myplayer')
            self.assertIn('Do you want to create the collection?', out)

        self.assertIsConvertedOgg(external_bach)
        self.assertFalse(os.path.isfile(external_beet))

        self.runcli('modify', '--yes', 'composer=JSB', 'artist:Bach')
        self.runcli('alt', 'update', 'myplayer')
        mediafile = MediaFile(external_bach)
        self.assertEqual(mediafile.composer, 'JSB')

        self.runcli('modify', '--yes', 'onplayer!', 'artist:Bach')
        self.runcli('modify', '--yes', 'onplayer=true', 'artist:Beethoven')
        self.runcli('alt', 'update', 'myplayer')

        self.assertFalse(os.path.isfile(external_bach))
        self.assertIsConvertedOgg(external_beet)

    def assertIsConvertedOgg(self, path):
        with open(path, 'r') as f:
            f.seek(-5, os.SEEK_END)
            self.assertEqual(f.read(), 'ISOGG')


class ExternalCopyCliTest(TestHelper, TestCase):

    def setUp(self):
        super(ExternalCopyCliTest, self).setUp()
        self.external_dir = self.mkdtemp()
        self.config['alternatives']['external'] = {
            'myexternal': {
                'directory': self.external_dir,
                'query': 'myexternal:true',
            }
        }
        self.external_config = \
            self.config['alternatives']['external']['myexternal']

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


class ExternalRemovableTest(TestHelper, TestCase):

    def setUp(self):
        super(ExternalRemovableTest, self).setUp()
        external_dir = os.path.join(self.mkdtemp(), u'\u00e9xt')
        self.config['alternatives']['external'] = {
            'myexternal': {
                'directory': external_dir,
                'query': '',
            }
        }
        self.external_config = \
            self.config['alternatives']['external']['myexternal']

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
