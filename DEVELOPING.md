# Developer Guide

This project uses [Poetry][] for packaging and dependency management.

We’re using the following tools to ensure consistency and quality

- [ruff](https://docs.astral.sh/ruff/)
- [pyright](https://microsoft.github.io/pyright/)
- [pytest](https://docs.pytest.org/)

```bash
poetry install
poetry run ruff check .
poetry run pyright .
poetry run pytest
```

[poetry]: https://python-poetry.org/

## Releasing

To release a new version of this project follow these steps:

1. Replace the “Upcoming” heading of the changelog with the new version number
   and date of release.
2. Update the version in `pyproject.toml`
3. Commit the changes with the commit message “Release vX.Y.Z” to `main`.
4. Tag the main branch with a signed and annotated tag: `git tag -as vX.Y.Z`.
   Use the version and date as the tag title and the changelog entry as the tag body. E.g.

   ```
   v0.10.0 - 2019-08-25

   * Symlink views now support relative symlinks (@daviddavo)
   ```

5. Push the main branch and tag with `git push --tags`
6. Create a release on Github using the version as the title and the changelog
   entries as the description.
7. Publish the new version to PyPI with `poetry publish`.
