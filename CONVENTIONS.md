# Conventions

Rules that are cheap to follow and cost nothing to keep. `python tools/conventions.py check` verifies the ones marked (checked); the rest are judgement.

## Commits

- Conventional commits with one of: `feat fix docs test chore ci build style refactor perf`. Optional scope: `fix(term): ...`. (checked)
- Subject at most 100 characters, in the imperative, no trailing period. One logical change per commit. (type and length checked)
- Author is the repo's `git config user.*`. No AI credit lines. (checked)

## Branches

- `main` is always green and releasable. (default branch checked)
- Work happens on short-lived `type/topic` branches, for example `feat/mcmc-posterior`. Delete after merge.

## Python

- Ruff is the linter and formatter. `line-length = 100`, and `lint.select` includes `I`, `UP` and `B` on top of the defaults. (checked)
- CI runs `ruff check` and `ruff format --check`. (checked)
- Type hints on public functions. No type checker is required yet.
- `uv.lock` is committed. (checked)
- The build backend is `uv_build`, except in a repo with a Termux CI job (`termux.yml`): uv_build cannot be built from source on Android, so `pip install .` fails there and such a repo keeps hatchling. (checked)
- A package with `src/` has `tests/` run by pytest in CI. (checked)
- `requires-python` is the lowest version CI actually tests. Packages published to PyPI do not use a pre-release bound.

## README

- Install and first-run steps come first, in order, as code blocks. Each block a new user should run is marked with `<!-- test -->` (or `windows`, `unix`, `console`) on the line before it. (checked)
- CI runs the marked blocks on Linux, macOS and Windows by calling the shared workflow: `uses: NaNIsaac/.github/.github/workflows/readme.yml@main`. (checked)
- `README.md` is English and `README.ko.md` mirrors it, marks included.

## Releases

- Tag `vX.Y.Z` and write GitHub release notes.
- Keep a `CHANGELOG.md` only for libraries published to PyPI.
