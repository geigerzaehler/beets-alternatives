Developer Guide
===============

Releasing
---------

To release a new version of this project follow these steps:

1. Replace the “Upcoming” heading of the changelog with the new version number
   and date of release.
2. Update the version in `setup.py`
3. Commit the changes with the commit message “Release vX.Y.Z” to `master`.
4. Tag the master branch with a signed and annotated tag: `git tag -as vX.Y.Z`.
   Use the version and date as the tag title and the changelog entry as the tag body. E.g.
   ```
   v0.10.0 - 2019-08-25

   * Symlink views now support relative symlinks (@daviddavo)
   ```
5. Push the master branch and tag with `git push --tags`
6. Create a release on Github using the version as the title and the changelog
   entries as the description.
7. Upload the new version to PyPI with the following commands
   ```
   rm dist
   python3 setup.py sdist bdist_wheel
   twine upload dist/*
   ```
