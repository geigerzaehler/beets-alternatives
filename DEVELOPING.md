# Developer Guide

This project uses [uv][] for packaging and dependency management.

We’re using the following tools to ensure consistency and quality

- [ruff](https://docs.astral.sh/ruff/)
- [pyright](https://microsoft.github.io/pyright/)
- [pytest](https://docs.pytest.org/)

```bash
uv sync
uv run ruff check .
uv run pyright .
uv run pytest
```

[uv]: https://docs.astral.sh/uv/

## Releasing

To release a new version of this project follow these steps:

1. `git fetch && git checkout -B release/candidate origin/main`
1. Replace the “Upcoming” heading of the changelog with the new version number
   and date of release.
1. Update the version in `pyproject.toml`
1. Commit the changes with the commit message “release: vX.Y.Z”
1. Push the changes `git push origin` and wait for the build to pass.
1. Wait for the release workflow to publish the release
