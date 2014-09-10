import os
import os.path
from unittest import TestCase

from helper import TestHelper

from beets.mediafile import MediaFile


class DocTest(TestHelper, TestCase):

    def setUp(self):
        super(DocTest, self).setUp()
        self.add_album(artist='Bach')
        self.add_album(artist='Beethoven')

    def test_external(self):
        external_dir = self.mkdtemp()
        self.config['convert']['formats'] = {
            'ogg': 'cp $source $dest; printf ISOGG >> $dest'
        }
        self.config['alternatives']['external'] = {
            'myplayer': {
                'directory': external_dir,
                'paths': {'default': '$artist/$title'},
                'format': 'ogg',
                'query': 'onplayer:true'
            }
        }

        external_bach = os.path.join(external_dir, 'Bach', 'track 1.ogg')
        external_beet = os.path.join(external_dir, 'Beethoven', 'track 1.ogg')

        self.runcli('modify', '--yes', 'onplayer=true', 'artist:Bach')
        self.runcli('alt', 'update', 'myplayer')

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
        external_dir = self.mkdtemp()
        self.config['alternatives']['external'] = {
            'myexternal': {
                'directory': external_dir,
                'paths': {'default': '$artist/$title'},
                'query': 'myexternal:true'
            }
        }
        self.external_config = \
            self.config['alternatives']['external']['myexternal']

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
