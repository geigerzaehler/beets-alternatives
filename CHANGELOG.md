# Change Log

## v0.14.0 - 2025-11-16

- Added `album_art_copy` option which copies album art to alternative
  collections ([@maxaudron][])
- Added options to resize and reformat embedded or copied album art ([@maxaudron][])
- Require beets >= 2.1

[@maxaudron]: https://github.com/maxaudron

## v0.13.4 - 2025-09-13

- Promote `typing-extensions` to runtime dependency
- Improved disk write performance when encoding items
- You can now hook into collection updates and run your own logic using the
  `alternatives.item_updated` event.

## v0.13.3 - 2025-05-12

- Add support for beets v2.3.x

## v0.13.2 - 2025-02-28

- Make builds more reliable for packagers

## v0.13.1 - 2024-11-17

- Resize embedded art in alternative files with `albumart_maxwidth` option.

## v0.13.0 - 2024-08-17

- Consistently use Unicode paths to alternative items. This may result and
  collection updates and orphaned files in alternatives. It may also improve
  usability on non-standard file systems (see [#74]).
- Require Python >= 3.10

[#74]: https://github.com/geigerzaehler/beets-alternatives/issues/74

## v0.12.0 - 2024-06-25

- Fix an issue where items in a symlink collection with relative links were
  always unnecessarily updated.
- Support [Beets v2](https://beets.readthedocs.io/en/latest/changelog.html#may-30-2024)

## v0.11.1 - 2024-04-24

- Add `--all` flag to update command which will update all configured
  collections.

## v0.11.0 - 2023-06-06

- Use the convert’s plugin [`thread` configuration][convert-config] when
  transcoding files. ([@johnyerhot](https://github.com/johnyerhot))
- Drop support for Python 2. Require Python >= 3.8
- Require beets >= 1.6.0

[convert-config]: https://beets.readthedocs.io/en/latest/plugins/convert.html#configuration

## v0.10.2 - 2020-07-15

- Add `beet alt list-tracks` command
- SymlinkView: Fix stale symlinks not being removed when files are moved in the
  main library [#47][]

[#47]: https://github.com/geigerzaehler/beets-alternatives/issues/47

## v0.10.1 - 2019-09-18

- Running `beet completion` does not crash anymore [#38][]

[#38]: https://github.com/geigerzaehler/beets-alternatives/issues/38

## v0.10.0 - 2019-08-25

- Symlink views now support relative symlinks (@daviddavo)
- Running just `beet alt` does not throw an error anymore (@daviddavo)

## v0.9.0 - 2018-11-24

- The package is now on PyPI
- Require at least beets v1.4.7
- Update album art in alternatives when it changes
- Python 3 support (Python 2.7 continuous to be supported)
- Support the format aliases defined by the convert plugin ('wma' and 'vorbis'
  with current beets)
- Bugfix: Explicitly write tags after encoding instead of relying on the
  encoder to do so
- Bugfix: If the `formats` config option is modified, don't move files if the
  extension would change, but re-encode

## v0.8.2 - 2015-05-31

- Fix a bug that made the plugin crash when reading unicode strings
  from the configuration

## v0.8.1 - 2015-05-30

- Require beets v1.3.13 and drop support for all older versions.
- Embed cover art when converting items

## v0.8.0 - 2015-04-14

First proper release
