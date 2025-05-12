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

1. `git fetch && git checkout -B release/candidate origin/main`
1. Replace the “Upcoming” heading of the changelog with the new version number
   and date of release.
1. Update the version in `pyproject.toml`
1. Commit the changes with the commit message “Release vX.Y.Z”
1. Push the changes `git push origin` and wait for the build to pass.
1. Tag the branch with a signed and annotated tag: `git tag -as vX.Y.Z`.
   Use the version and date as the tag title and the changelog entry as the tag
   body. E.g.

   ```plain
   v0.10.0 - 2019-08-25

   * Symlink views now support relative symlinks (@daviddavo)
   ```

1. Push the main branch and tag with `git push --tags`
1. Create a release on Github using the version as the title and the changelog
   entries as the description.
1. Publish the new version to PyPI with `poetry publish`.
1. Integrate the release branch

   ```bash
   git checkout main
   git merge --ff release/candidate
   git push origin
   ```
