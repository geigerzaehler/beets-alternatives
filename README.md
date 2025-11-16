beets-alternatives
==================

[![Check and test](https://github.com/geigerzaehler/beets-alternatives/actions/workflows/main.yaml/badge.svg)](https://github.com/geigerzaehler/beets-alternatives/actions/workflows/main.yaml)
[![Coverage Status](https://coveralls.io/repos/github/geigerzaehler/beets-alternatives/badge.svg?branch=main)](https://coveralls.io/github/geigerzaehler/beets-alternatives?branch=main)

You want to manage multiple versions of your audio files with beets?
Your favorite iPlayer has limited space and does not support Ogg Vorbis? You
want to keep lossless versions on a large external drive? You want to
symlink your audio to other locations?

With this [beets][beets-docs] plugin every file in you music library have
multiple alternate versions in separate locations.

If you’re interested in contributing to this project, check out the [developer
documentation](./DEVELOPING.md).

Getting Started
---------------

Install the plugin and make sure you using at least version 2.1 of beets and
Python 3.10.

```bash
pip install --upgrade "beets>=2.1" beets-alternatives
```

Then, [enable the plugin][using plugins]. You may use the `beet config --edit`
command to add the *alternatives* plugin to the configuration.

```yaml
plugins:
- ...
- alternatives
```

Now, you can get rolling with one of the use cases below.

### External Files

Suppose your favorite portable player only supports MP3 and MP4, has
limited disk space and is mounted at `/player`. Instead of selecting
its content manually and using the `convert` plugin to transcode it, you
want to sync it automatically. First we give this external collection
the name ‘myplayer’ and start configuring beets.

```yaml
alternatives:
  myplayer:
    directory: /player
    paths:
      default: $album/$title
    formats: aac mp3
    query: "onplayer:true"
    removable: true
```

The first two options determine the location of the external files and
correspond to the global [`directory`][config-directory] and
[`paths`][config-paths] settings.  The `format` option specifies the
formats we transcode the files to (more on that below).  Finally, the
`query` option tells the plugin which files you want to put in the
external location. The value is a [query string][] as used for the
beets command line. In our case we use a flexible attribute to make the
selection transparent.

Let’s add some files to our selection by setting the flexible attribute
from the `query` option. (Since we use boolean values for the
‘onplayer’ field it might be a good idea to set the type of this field
to `bool` using the *types* plugin)

```bash
beet modify onplayer=true artist:Bach
```

The configured query also matches all tracks that are part of an album
where the `onplayer` attribute is ‘true’. We could also use

```bash
beet modify -a onplayer=true albumartist:Bach
```

We then tell beets to create the external files.

```
$ beet alt update myplayer
Collection at '/player' does not exists. Maybe you forgot to mount it.
Do you want to create the collection? (y/N)
```

The question makes sure that you don’t recreate a external collection
if the device is not mounted. Since this is our first go, we answer the
question with yes.

The command will copy all files with the artist ‘Bach’ and format either ‘AAC’
or ‘MP3’ to the `/player` directory. All other formats will be transcodec to the
‘AAC’ format unsing the [*convert* plugin][convert plugin]. The transcoding
process can be configured through [*convert’s* configuration][convert config].

If you update some tracks in your main collection, the `alt update`
command will propagate the changes to your external collection.  Since
we don’t need to convert the files but just update the tags, this will
be much faster the second time.

```bash
beet modify composer="Johann Sebastian Bach" artist:Bach
beet alt update myplayer
```

After going for a run you mitght realize that Bach is probably not the
right thing to work out to. So you decide to put Beethoven on your
player.

```bash
beet modify onplayer! artist:Bach
beet modify onplayer=true artist:Beethoven
beet alt update myplayer
```

This removes all Bach tracks from the player and adds Beethoven’s.

### Symlink Views

Instead of copying and converting files this plugin can also create
symbolic links to the files in your library. For example you want to
have a directory containing all music sorted by year and album.

```yaml
directory: /music
paths:
  default: $artist/$album/$title

alternatives:
  by-year:
    directory: by-year
    paths:
      default: $year/$album/$title
    formats: link
```

The first thing to note here is the `link` format. Instead of
converting the files this tells the plugin to create symbolic links to
the original audio file.  We also note that the directory is a relative
path: it will be resolved with respect to the global `directory`
option. We could also omit the directory configuration as it defaults
to the collection’s name. Finally, we omitted the `query` option. This
means that we want to create symlinks for all files. Of course you can
still add a query to select only parts of your collection.

The `beet alt update by-year` command will now create the symlinks. For
example

```plain
/music/by-year/1982/Thriller/Beat It.mp3
-> /music/Michael Jackson/Thriller/Beat It.mp3
```

You can also specify if you want absolute symlinks (default) or relative ones
with `link_type`. The option `link_type` must be `absolute` or `relative`

```yaml
alternatives:
  by-year:
    directory: by-year
    paths:
      default: $year/$album/$title
    formats: link
    link_type: relative
```

With this config, the `beet alt update by-year` command will create relative
symlinks. E.g:

```plain
/music/by-year/1982/Thriller/Beat It.mp3
-> ../../../Michael Jackson/Thriller/Beat It.mp3
```

Now, if you move the `/music/` folder to another location, the links
will continue working

CLI Reference
-------------

```plain
beet alt update [--create|--no-create] NAME
```

Updates the external collection configured under `alternatives.NAME`.

* Add missing files. Convert them to the configured format or copy
  them.

* Remove files that don’t match the query but are still in the
  external collection

* Move files to the path determined from the `paths` configuration.

* Update tags if the modification time of the external file is older
  than that of the source file from the library.

The command accepts the following option.

* **`--[no-]create`** If the `removable` configuration option
  is set and the external base directory does not exist, then the
  command will ask you to confirm the creation of the external
  collection. These options specify the answer as a cli option.

```plain
beet alt update [--create|--no-create] --all
```

Update all external collections defined in `alternatives` configuration.

```plain
beet alt list-tracks [--format=FORMAT] NAME
```

Lists all tracks that are currently included in the collection.

The `--format` option accepts a [beets path format][path-format] string that is
used to format each track.

[path-format]: https://beets.readthedocs.io/en/latest/reference/pathformat.html

Configuration
-------------

An external collection is configured as a name-settings-pair under the
`alternatives` configuration. The name is used to reference the
collection from the command line. The settings is a map of the
following settings.

* **`directory`** The root directory to store the external files under.
  Relative paths are resolved with respect to the global `directory`
  configuration. If omitted it defaults to the name of the collection
  and is therefore relative to the library directory. (optional)

* **`paths`** Path templates for audio files under `directory`. Configured
  like the [global paths option][config-paths] and defaults to it if
  not given. (optional)

* **`query`** A [query string][] that determine which tracks belong to the
  collection. A track belongs to the collection if itself or the album
  it is part of matches the query. To match all items, specify an empty
  string. (required)

* **`formats`** A list of space separated strings that determine the
  audio file formats in the external collection. If the ‘format’ field
  of a track is included in the list, the file is copied. Otherwise,
  the file is transcoded to the first format in the list. The name of
  the first format must correpond to a key in the
  [`convert.formats`][convert plugin] configuration. This configuration
  controls the transcoding process.

  The special format ‘link’ is used to create symbolic links instead of
  transcoding the file. It can not be combined with other formats.

  By default no transcoding is done.

* **`removable`** If this is `true` (the default) and `directory` does
  not exist, the `update` command will ask you to confirm the creation
  of the external collection. (optional)

* **`link_type`** Can be `absolute` (default) or `relative`. If
  **`formats`** is `link`, it sets the type of links to create. For
  differences between link types and examples see [Symlink Views](#symlink-views).

* **`album_art_embed`** Embed album art into the media file. Default `yes`

* **`album_art_copy`** Copy album art files into the collection. If
  `formats: link` is used then album art is linked instead. Filename for
  cover art is determined by the [art_filename][art_filename] option.

* **`album_art_maxwidth`** If set, resize album art to this maximum width while
  preserving aspect ratio. Comparable to the [convert plugin][convert plugin]
  setting with the same name.

* **`album_art_format`** If set, convert album art to the specified format (e.g.
  "jpg"). Supports the same values as [`fetchart.cover_formats`][cover formats].

* **`album_art_deinterlace`** If true, JPEG album art is encoded as
  a non-progressive image. Enable this if your device does not support
  progressive images.

* **`album_art_quality`** JPEG quality level (1–100) when compressing images
  (requires `album_art_max_width`). Use 0 for default quality. 65–75 is
  typically a good range. The default behavior depends on the backend:
  ImageMagick estimates input quality (using 92 if unknown), PIL uses 75.
  Default: 0 (disabled) (optional)

Events
------

The plugin emits the `alternatives.item_updated` event after an item (track) is
added, removed or updated in a collection by the `update` command.You can use
the [Hook plugin][] to run a shell command whenever an item is updated.

```yaml
hook:
  hooks:
  - event: alternatives.item_updated
    command: "bash -c 'echo \"{collection}: {action} {item.path}\" >> events.log"
```

Alternatively, you can [listen for events][] from a custom plugin.

The event listener receives the following arguments:

* `collection: str` — name of the collection
* [`item: beets.Item`][Item] — library item the action is taken on
* `path: str` — absolute path of the item in the collection
* `action: str` — type of update action:
  * `ADD`: The item is added to the collection and was not present before
  * `REMOVE`: The item is removed from the collection
  * `MOVE`: The file for an item is moved to a different location in the collection
  * `WRITE`: Updated metadata is written to the file in the collection
  * `SYNC_ART`: Updated album art is written to the file in the collection

[hook plugin]: https://beets.readthedocs.io/en/stable/plugins/hook.html
[listen for events]: https://beets.readthedocs.io/en/stable/dev/plugins.html#listen-for-events
[Item]: https://beets.readthedocs.io/en/stable/dev/library.html#beets.library.Item

Feature Requests
----------------

If you have an idea or a use case this plugin is missing, feel free to
[open an issue](https://github.com/geigerzaehler/beets-alternatives/issues/new).

The following is a list of things I might add in the feature.

* Symbolic links for each artist in a multiple artists release (see the
  [beets issue][beets-issue-split-symlinks])

License
-------

Copyright (c) 2014-2023 Thomas Scholtes.

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"), to
deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

[beets-docs]: https://beets.readthedocs.io/en/latest/index.html
[beets-issue-split-symlinks]: https://github.com/sampsyo/beets/issues/153
[config-directory]: http://beets.readthedocs.org/en/latest/reference/config.html#directory
[config-paths]: http://beets.readthedocs.org/en/latest/reference/config.html#path-format-configuration
[convert config]: http://beets.readthedocs.org/en/latest/plugins/convert.html#configuring-the-transcoding-command
[convert plugin]: http://beets.readthedocs.org/en/latest/plugins/convert.html
[query string]: http://beets.readthedocs.org/en/latest/reference/query.html
[using plugins]: http://beets.readthedocs.org/en/latest/plugins/index.html#using-plugins
[cover formats]: https://beets.readthedocs.io/en/stable/plugins/fetchart.html#image-formats
[art_filename]: https://beets.readthedocs.io/en/stable/reference/config.html#art-filename
