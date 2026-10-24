# Conventions

Rules that are cheap to follow and cost nothing to keep. `python tools/conventions.py check` verifies the ones marked (checked); the rest are judgement.

## Commits

- Conventional commits with one of: `feat fix docs test chore ci build style refactor perf`. Optional scope: `fix(term): ...`. (checked)
- Subject at most 100 characters, in the imperative, no trailing period. One logical change per commit. (type and length checked, for commits made since 2026-10-22: earlier history cannot be rewritten)
- Author is the repo's `git config user.*`. No AI credit lines. (checked)

## Branches

Trunk-based, one long-lived branch. This is the whole model; there is no `dev`, `release` or
`staging` branch, because one person (with agents) integrates continuously and a second
integration branch only adds a place for work to go stale.

- `main` is the only long-lived branch. It is always green and releasable. (default branch checked)
- Every change is a short-lived `type/topic` branch (`feat/mcmc-posterior`, `fix/q-components`,
  `docs/site`), opened as a pull request and **squash-merged**. One item of work, one branch, one PR.
- Branches die with their PR: `delete_branch_on_merge` is on in every repository (checked), and
  local clones run with `git config --global fetch.prune true` so deleted remotes disappear on fetch.
- A branch should merge within days. Stacking a PR on another PR is allowed only when the base
  PR will merge first on the same day; otherwise wait or fold the change into one PR.
- Research branches (`research/<topic>`) are the one exception to "days": they may live for weeks,
  but only with an open draft PR so the work is visible, and they end in one of two ways: merged
  as documentation (the notes, figures and scripts worth keeping), or frozen as a tag
  `archive/<topic>` and deleted. A research branch is never a second `main`.
- Releases are tags on `main` (`vX.Y.Z`); see Releases. Nothing is released from a branch.
- Agents follow the same rules with two additions: a branch is created only for a claimed
  BACKLOG item, and a worktree holds exactly one branch and is removed when the PR merges.

## Python

- Ruff is the linter and formatter. `line-length = 100`, and `lint.select` includes `I`, `UP` and `B` on top of the defaults. (checked)
- CI runs `ruff check` and `ruff format --check`. (checked)
- Type hints on public functions. No type checker is required yet.
- `uv.lock` is committed. (checked)
- The build backend is `uv_build`, with three exceptions the checker knows: a compiled extension keeps `scikit_build_core` (with a `CMakeLists.txt`) or `mesonpy` (with a `meson.build`), since uv_build packs only pure Python; a repo with a Termux CI job (`termux.yml`) keeps hatchling, since uv_build cannot be built from source on Android; and any other reason is declared in the repo as `[tool.conventions] build-backend = "<reason>"` in `pyproject.toml`. (checked)
- A package with `src/` has `tests/` run by pytest in CI. (checked)
- `requires-python` is the lowest version CI actually tests. Packages published to PyPI do not use a pre-release bound.
- A repo that depends on `genx3` excludes `wxpython` on Linux, because PyPI has no Linux wheel for it and building it fails without GTK headers: `[tool.uv] override-dependencies = ["wxpython; sys_platform != 'linux'"]`. The repo does not start the GenX GUI, so nothing is lost.
- A repo that takes `torch` from the `pytorch-cu128` index restricts the source with `marker = "sys_platform != 'darwin'"` (or `== 'win32'`), because that index has no macOS wheels and `uv sync` cannot resolve there. The README job runs on macOS for public repos.

## Comments and GitHub text

- Code comments, docstrings, commit messages, pull requests, issues and review comments are in English. The exceptions are `README.ko.md` and repos whose subject is the Korean language (`hangeul`, `hanfix`). (Korean in comments and docstrings checked, outside those repos)
- A comment says why, which the code cannot. No numbered step comments (`# 1.`, `# Step 2:`), no section banners (`# ---- Load ----`), no comment that restates the line below, no `Note:` or `Important:` emphasis. (step, banner and `Note:`/`Important:` comments checked in Python, PowerShell, shell and C-like sources)
- A file you touch loses such comments; deleting is the default, translating only what still earns its place.

## README

- Install and first-run steps come first, in order, as code blocks. Each block a new user should run is marked with `<!-- test -->` (or `windows`, `unix`, `console`) on the line before it. (checked)
- CI runs the marked blocks on Linux, macOS and Windows by calling the shared workflow: `uses: NaNIsaac/.github/.github/workflows/readme.yml@main`. (checked)
- `README.md` is English and `README.ko.md` mirrors it, marks included.

## Releases

- Tag `vX.Y.Z` and write GitHub release notes.
- Keep a `CHANGELOG.md` only for libraries published to PyPI.

## Exemptions

- A repo that cannot follow a checked rule says so in `.conventions.toml` at its root, with the reason as the value, and `check` stops reporting that rule for it. Only the rules named here can be excused; a rule with an empty reason is not excused.

  ```toml
  [exempt]
  readme-tests = "the README steps change this machine and cannot run on CI"
  readme-language = "the project is about the Korean language and README.md is Korean"
  ```

  `readme-tests` covers the setup steps (not tested, not run in CI); `readme-language` covers an English `README.md`. The build backend keeps its own declaration, `[tool.conventions] build-backend` in `pyproject.toml`.
