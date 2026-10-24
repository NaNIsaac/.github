"""Repo conventions across D:\\03_Develop\\NaNIsaac.

    python conventions.py check [repo ...]
    python conventions.py fix   [repo ...]   # commits on a branch in a worktree, never pushes

check is read-only. fix handles only the mechanical rules. For each repo with something to
fix it makes a worktree under _wt/conventions/<repo> on a new branch from origin/main and
commits there, so the shared checkout and its main are never touched; push the branch and
open a PR from it. Rules that need judgement are reported, not fixed.
"""

import ast
import io
import json
import re
import subprocess
import sys
import tempfile
import tokenize
import tomllib
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNER, EMAIL = "Isaac Yong", "isaacyong@naver.com"
LEGACY_EMAILS = (
    "esakyong1866@naver.com",
    "isacyong@naver.com",
    "114078872+SJB7777@users.noreply.github.com",
    "114078872+NaNIsaac@users.noreply.github.com",
)
OWN_FORKS = {"NaNIsaac.github.io"}
NO_LICENSE = {".github", "NaNIsaac", "Life"}
NO_COMMIT_FORMAT = {"Life"}
# Checkouts that are not at ROOT/<name> because that path is taken: ROOT/docs is the notes folder.
LOCAL_PATHS = {"docs": "docs-site"}
RUFF_RULES = {"I", "UP", "B"}
COMMIT_SUBJECT = re.compile(
    r"(feat|fix|docs|test|chore|ci|build|style|refactor|perf)(\([\w./-]+\))?!?: \S"
)
AI_TRAILER = r"co-authored-by:.*(claude|anthropic)|generated with.*claude"
SETUP_HEADING = re.compile(
    r"^#{1,3} .*(install|quick ?start|getting started|setup|설치)", re.IGNORECASE | re.MULTILINE
)
README_MARK = re.compile(r"^<!-- test( (windows|unix|console))? -->$", re.MULTILINE)
# Repos whose subject is the Korean language may keep Korean comments; the other comment rules still apply.
KOREAN_SUBJECT = {"hangeul", "hanfix"}
C_LIKE = (
    ".rs",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".c",
    ".cc",
    ".cpp",
    ".cu",
    ".h",
    ".hpp",
    ".go",
    ".java",
)
COMMENT_MARKER = {".py": "#", ".ps1": "#", ".sh": "#"} | dict.fromkeys(C_LIKE, "//")
HANGUL = re.compile(r"[가-힣]")
STEP_COMMENT = re.compile(r"(\d+[.)]\s|step\s*\d+\b)", re.IGNORECASE)
BANNER_COMMENT = re.compile(r"[-=*#~_]{4,}|[-=*]{3,}\s.*\S\s[-=*]{3,}$")
EMPHASIS_COMMENT = re.compile(r"(note|important)\s*:", re.IGNORECASE)
COMMENT_RULES = {
    "korean": "comments or docstrings in Korean",
    "step": "numbered step comments",
    "banner": "banner comments",
    "emphasis": "Note:/Important: comments",
}
SKIPPED_SOURCE = re.compile(r"(^|/)(vendor|third_party|node_modules)/|\.min\.")

MIT = """MIT License

Copyright (c) {years} {owner}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def run(*args, cwd=None):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout


def git(repo, *args):
    return run("git", *args, cwd=repo)


def fix_worktree(repo, name):
    """A new worktree of `repo` on its own branch from origin/main, or None when it cannot
    be made. The folder keeps the repo name so name-based rules still apply."""
    path = ROOT / "_wt" / "conventions" / name
    branch = f"chore/conventions-{date.today():%Y%m%d}"
    if path.exists():
        print(f"{name}: {path} already exists, fixes skipped")
        return None
    git(repo, "fetch", "-q", "origin", "main")
    done = subprocess.run(
        ["git", "worktree", "add", "-q", "-b", branch, str(path), "origin/main"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        print(f"{name}: no worktree ({done.stderr.strip()}), fixes skipped")
        return None
    return path


def drop_worktree(repo, path):
    branch = git(path, "branch", "--show-current").strip()
    git(repo, "worktree", "remove", "--force", str(path))
    git(repo, "branch", "-D", branch)


def hangul_ratio(text):
    letters = len(re.findall(r"[A-Za-z가-힣]", text))
    return len(re.findall(r"[가-힣]", text)) / letters if letters else 0.0


def python_comments(text):
    """(comments, docstrings, parsed) of a Python source, each a list of (line, text).

    Comments come from the tokenizer, which reads syntax this Python does not know. Docstrings
    need the parser, so a source it rejects (a newer Python than the one running this) has
    none and `parsed` is False; None replaces the whole result when even tokenizing fails."""
    try:
        comments = [
            (tok.start[0], tok.string[1:].strip())
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, SyntaxError):
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return comments, [], False
    docstrings = [
        (node.body[0].lineno, doc)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and (doc := ast.get_docstring(node, clean=False))
    ]
    return comments, docstrings, True


def line_comments(suffix, text):
    """(line, text) for each comment that starts after whitespace or at the start of a line, plus
    one entry per block comment, where a Korean word is the only thing looked for."""
    marker = re.escape(COMMENT_MARKER[suffix])
    pattern = re.compile(rf"(?:^|\s){marker}(.*)$")
    comments = [
        (number, m[1].lstrip("/!").strip())
        for number, line in enumerate(text.splitlines(), 1)
        if (m := pattern.search(line))
    ]
    docstrings = (
        [
            (text.count("\n", 0, m.start()) + 1, m[0])
            for m in re.finditer(r"/\*.*?\*/", text, re.DOTALL)
        ]
        if suffix in C_LIKE
        else []
    )
    return comments, docstrings, True


def comment_hits(suffix, text, korean_allowed=False):
    """(rule, line) for each breach of the comment rules (BACKLOG rule 27) in one source file."""
    found = python_comments(text) if suffix == ".py" else line_comments(suffix, text)
    if found is None:
        return [("unparsed", 1)]
    comments, docstrings, parsed = found
    hits = [] if parsed else [("unparsed", 1)]
    for line, body in comments:
        if body.startswith(("noqa", "type:")):
            continue
        if STEP_COMMENT.match(body):
            hits.append(("step", line))
        if BANNER_COMMENT.match(body):
            hits.append(("banner", line))
        if EMPHASIS_COMMENT.match(body):
            hits.append(("emphasis", line))
    if not korean_allowed:
        hits += [("korean", line) for line, body in comments + docstrings if HANGUL.search(body)]
    return sorted(hits, key=lambda h: h[1])


def comment_findings(repo, name):
    """One message per rule with the count and the first place, over the tracked source files."""
    hits = {}
    for path in git(repo, "ls-files", "-z").split("\0"):
        suffix = Path(path).suffix
        if (
            suffix not in COMMENT_MARKER
            or SKIPPED_SOURCE.search(path)
            or not (repo / path).is_file()
        ):
            continue
        text = (repo / path).read_text(encoding="utf-8", errors="replace")
        for rule, line in comment_hits(suffix, text, name in KOREAN_SUBJECT):
            hits.setdefault(rule, []).append(f"{path}:{line}")
    if unread := hits.get("unparsed"):
        print(
            f"{name}: {len(unread)} Python files could not be parsed by this Python, so their "
            f"docstrings were not checked (first {unread[0]})",
            file=sys.stderr,
        )
    for rule, label in COMMENT_RULES.items():
        if rule in hits:
            yield f"{len(hits[rule])} {label}, first at {hits[rule][0]}"


def copyright_years(repo):
    years = git(repo, "log", "--format=%ad", "--date=format:%Y").split()
    first, now = (min(years) if years else str(date.today().year)), str(date.today().year)
    return first if first == now else f"{first}-{now}"


def fix_license(repo):
    (repo / "LICENSE").write_text(
        MIT.format(years=copyright_years(repo), owner=OWNER), encoding="utf-8"
    )
    return "docs: add MIT license"


def fix_holder(repo, path):
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"(Copyright \(c\) [\d-]+ ).+", rf"\g<1>{OWNER}", text, count=1), encoding="utf-8"
    )
    return "docs: unify license copyright holder"


def fix_gitattributes(repo):
    path = repo / ".gitattributes"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        old.rstrip("\n") + ("\n" if old else "") + "* text=auto eol=lf\n", encoding="utf-8"
    )
    return "chore: normalize line endings"


def fix_mailmap(repo, emails):
    path = repo / ".mailmap"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = "".join(f"{OWNER} <{EMAIL}> <{e}>\n" for e in emails)
    path.write_text(old + lines, encoding="utf-8")
    return "chore: map former author emails"


def fix_ci_name(repo):
    git(repo, "mv", ".github/workflows/tests.yml", ".github/workflows/ci.yml")
    for readme in repo.glob("README*.md"):
        text = readme.read_text(encoding="utf-8")
        readme.write_text(text.replace("workflows/tests.yml", "workflows/ci.yml"), encoding="utf-8")
    return "ci: rename tests workflow to ci.yml"


DEPENDABOT = """version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: monthly
    groups:
      actions:
        patterns: ["*"]
"""


def fix_dependabot(repo):
    (repo / ".github" / "dependabot.yml").write_text(DEPENDABOT, encoding="utf-8")
    return "ci: update github actions monthly with dependabot"


LINT_WORKFLOW = """name: lint
on:
  push:
    branches: [main]
  pull_request:
jobs:
  ruff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.3.0
      - run: uvx ruff@{version} check
"""


def baseline_table(rules):
    lines = [
        "",
        "# violations present when ruff became mandatory in CI; remove entries as they are fixed",
        "[tool.ruff.lint.extend-per-file-ignores]",
    ]
    lines += [
        f"{json.dumps(f, ensure_ascii=False)} = {json.dumps(sorted(c))}"
        for f, c in sorted(rules.items())
    ]
    return "\n".join(lines) + "\n"


def fix_lint(repo):
    """Make ruff mandatory in CI: existing violations become a per-file baseline, new ones fail the build."""
    path = repo / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    if "extend-per-file-ignores" in text:
        print(f"{repo.name}: lint skipped, per-file-ignores already configured")
        return None
    version = run("uvx", "ruff", "--version").split()[1]
    raw = run("uvx", f"ruff@{version}", "check", "--no-cache", "--output-format", "json", cwd=repo)
    found = json.loads(raw or "[]")
    if any(item["code"] is None for item in found):
        print(f"{repo.name}: lint skipped, syntax errors ruff cannot ignore")
        return None
    rules = {}
    for item in found:
        rel = Path(item["filename"]).resolve().relative_to(repo.resolve()).as_posix()
        rules.setdefault(rel, set()).add(item["code"])
    workflow = repo / ".github" / "workflows" / "lint.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(LINT_WORKFLOW.format(version=version), encoding="utf-8")
    if rules:
        path.write_text(text.rstrip("\n") + "\n" + baseline_table(rules), encoding="utf-8")
    if subprocess.run(
        ["uvx", f"ruff@{version}", "check", "--no-cache"],
        cwd=repo,
        capture_output=True,
        check=False,
    ).returncode:
        git(repo, "checkout", "--", "pyproject.toml")
        workflow.unlink()
        print(f"{repo.name}: lint reverted, ruff still fails with the baseline")
        return None
    # A repo with a workflow is checked for dependabot.yml, so a first workflow brings it along.
    if not (repo / ".github" / "dependabot.yml").exists():
        fix_dependabot(repo)
        return "ci: run ruff on every push and update actions with dependabot"
    return "ci: run ruff on every push"


def fix_ruff(repo):
    path = repo / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.rstrip("\n") + "\n\n[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    return "chore: add ruff config"


def wheel_files(repo, out):
    """Build a wheel into out and return its file names, without the dist-info metadata."""
    done = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out), str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    wheels = list(Path(out).glob("*.whl"))
    if done.returncode or not wheels:
        return None
    with zipfile.ZipFile(wheels[0]) as z:
        return {n for n in z.namelist() if ".dist-info/" not in n and not n.endswith("/")}


def rewrite_tables(text, tables):
    """Replace or drop whole TOML tables, keyed by header line; everything else stays byte for byte."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            skipping = stripped in tables
            if skipping:
                out.append(tables[stripped])
                continue
        elif skipping and not stripped:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


# Backends that build compiled code, and the build file that shows the repo needs one.
COMPILED_BACKENDS = {"scikit_build_core.build": "CMakeLists.txt", "mesonpy": "meson.build"}


def backend_excused(repo, backend):
    """Whether a backend other than uv_build is the right one for this repo."""
    # uv_build only packs pure Python; a C++/CUDA extension needs its CMake or Meson backend.
    if (needs := COMPILED_BACKENDS.get(backend)) and (repo / needs).exists():
        return True
    # uv_build has no Android wheel and needs Rust to build there, so `pip install .`
    # fails in Termux; a repo tested in Termux keeps its pure-Python backend.
    if (repo / ".github" / "workflows" / "termux.yml").exists():
        return True
    # Any other reason is declared in the repo, with the reason as the value.
    declared = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return bool(
        str(declared.get("tool", {}).get("conventions", {}).get("build-backend", "")).strip()
    )


def fix_uv_build(repo):
    """Switch hatchling/setuptools to uv_build when the wheel stays identical; otherwise leave it."""
    path = repo / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    module = data["project"]["name"].replace("-", "_").lower()
    tool = data.get("tool", {})
    hatch = tool.get("hatch", {})
    wheel_packages = (
        hatch.get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("packages", [f"src/{module}"])
    )
    if not (repo / "src" / module / "__init__.py").exists() or "setuptools" in tool:
        print(f"{repo.name}: uv_build skipped, not a plain src/{module} package")
        return None
    if wheel_packages != [f"src/{module}"] or set(hatch) - {"build"}:
        print(f"{repo.name}: uv_build skipped, custom hatch build settings")
        return None
    version = run("uv", "--version").split()[1]
    minor = int(version.split(".")[1])
    new = rewrite_tables(
        text,
        {
            "[build-system]": f'[build-system]\nrequires = ["uv_build>={version},<0.{minor + 1}"]\nbuild-backend = "uv_build"\n\n',
            "[tool.hatch.build.targets.wheel]": "",
            "[tool.hatch.build.targets.sdist]": "",
        },
    )
    with tempfile.TemporaryDirectory() as tmp:
        before = wheel_files(repo, Path(tmp) / "before")
        path.write_text(new, encoding="utf-8")
        after = wheel_files(repo, Path(tmp) / "after")
    if before is None or after != before:
        git(repo, "checkout", "--", "pyproject.toml")
        print(
            f"{repo.name}: uv_build reverted, "
            + ("current build fails" if before is None else "wheel contents differ")
        )
        return None
    return "build: switch to uv_build"


def findings(repo, info):
    """Yield (message, fix) pairs; fix is a zero-argument callable or None."""
    name = info["name"]

    if info["defaultBranchRef"]["name"] != "main":
        yield f"default branch is {info['defaultBranchRef']['name']}", None

    licenses = [p for p in repo.glob("LICENSE*") if p.is_file()]
    if not licenses and info["visibility"] == "PUBLIC" and name not in NO_LICENSE:
        yield "public repo without LICENSE", lambda: fix_license(repo)
    for path in licenses:
        text = path.read_text(encoding="utf-8", errors="replace")
        holder = re.search(r"Copyright \(c\) [\d-]+ (.+)", text)
        if text.startswith("MIT License") and holder and holder[1].strip() != OWNER:
            yield f"copyright holder is {holder[1].strip()!r}", lambda p=path: fix_holder(repo, p)

    attrs = repo / ".gitattributes"
    if not attrs.exists() or "text=auto" not in attrs.read_text(encoding="utf-8", errors="replace"):
        yield "no 'text=auto' in .gitattributes", lambda: fix_gitattributes(repo)

    authors = set(git(repo, "log", "--all", "--format=%ae%n%ce").split())
    mailmap = (
        (repo / ".mailmap").read_text(encoding="utf-8") if (repo / ".mailmap").exists() else ""
    )
    legacy = [e for e in LEGACY_EMAILS if e in authors and e not in mailmap]
    if legacy:
        yield f"former emails in history: {', '.join(legacy)}", lambda: fix_mailmap(repo, legacy)

    if not info["description"]:
        yield "no GitHub description", None
    if not info["repositoryTopics"]:
        yield "no GitHub topics", None

    workflows = repo / ".github" / "workflows"
    if (workflows / "tests.yml").exists() and not (workflows / "ci.yml").exists():
        yield "tests.yml should be ci.yml", lambda: fix_ci_name(repo)

    if workflows.is_dir() and any(workflows.glob("*.yml")):
        if not (repo / ".github" / "dependabot.yml").exists():
            yield "workflows without .github/dependabot.yml", lambda: fix_dependabot(repo)
        lint = run(
            "uvx", "--quiet", "--from", "actionlint-py", "actionlint", "-oneline", cwd=repo
        ).strip()
        if lint:
            yield f"actionlint: {lint.splitlines()[0]}", None

    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        if "[tool.ruff" not in pyproject.read_text(encoding="utf-8"):
            yield "no [tool.ruff] in pyproject.toml", lambda: fix_ruff(repo)
        if not (repo / ".github" / "workflows" / "lint.yml").exists():
            yield "ruff is not enforced in CI (no lint.yml)", lambda: fix_lint(repo)
        backend = re.search(r'build-backend\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"))
        if backend and backend[1] != "uv_build" and not backend_excused(repo, backend[1]):
            yield f"build backend is {backend[1]}, not uv_build", lambda: fix_uv_build(repo)
        lint = (
            tomllib.loads(pyproject.read_text(encoding="utf-8"))
            .get("tool", {})
            .get("ruff", {})
            .get("lint", {})
        )
        missing = RUFF_RULES - set(lint.get("select", [])) - set(lint.get("extend-select", []))
        if "[tool.ruff" in pyproject.read_text(encoding="utf-8") and missing:
            yield f"ruff lint does not select {', '.join(sorted(missing))}", None
        lint_yml = repo / ".github" / "workflows" / "lint.yml"
        if (
            lint_yml.exists()
            and "ruff@" in lint_yml.read_text(encoding="utf-8")
            and "format" not in lint_yml.read_text(encoding="utf-8")
        ):
            yield "lint.yml does not run ruff format --check", None
        if not (repo / "uv.lock").exists():
            yield "no uv.lock", None
        if (repo / "src").is_dir() and not (repo / "tests").is_dir():
            yield "src/ without tests/", None

    bad = (
        []
        if name in NO_COMMIT_FORMAT
        else [
            s
            for s in git(repo, "log", "-n", "20", "--no-merges", "--format=%s").splitlines()
            if not COMMIT_SUBJECT.match(s) or len(s) > 100
        ]
    )
    if bad:
        yield f"{len(bad)} of the last 20 commits break the subject format, e.g. {bad[0]!r}", None

    readme = repo / "README.md"
    if not readme.exists():
        yield "no README.md", None
    elif hangul_ratio(readme.read_text(encoding="utf-8", errors="replace")) > 0.15:
        yield "README.md is not English (move to README.ko.md, write README.md in English)", None

    if readme.exists():
        text = readme.read_text(encoding="utf-8", errors="replace")
        if SETUP_HEADING.search(text):
            if not README_MARK.search(text):
                yield "README setup steps are not tested (no <!-- test --> blocks)", None
            elif not any(
                "readme.yml@" in w.read_text(encoding="utf-8", errors="replace")
                for w in (repo / ".github" / "workflows").glob("*.yml")
            ):
                yield "README tests are not run in CI (call NaNIsaac/.github readme.yml)", None

    for message in comment_findings(repo, name):
        yield message, None

    if git(repo, "log", "--all", "-i", "-E", f"--grep={AI_TRAILER}", "--format=%h"):
        yield "AI trailers in history (run deai)", None


def selftest():
    assert hangul_ratio("hello") == 0 and hangul_ratio("한글 abc") > 0.3
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        assert not backend_excused(repo, "scikit_build_core.build")
        (repo / "CMakeLists.txt").write_text("", encoding="utf-8")
        assert backend_excused(repo, "scikit_build_core.build") and not backend_excused(
            repo, "hatchling.build"
        )
        (repo / "pyproject.toml").write_text(
            "[tool.conventions]\nbuild-backend = 'reason'\n", encoding="utf-8"
        )
        assert backend_excused(repo, "hatchling.build")
    src = "[a]\nx = 1\n\n[b]\ny = 2\n\n[c]\nz = 3\n"
    assert rewrite_tables(src, {"[b]": ""}) == "[a]\nx = 1\n\n[c]\nz = 3\n"
    assert baseline_table({"a.py": {"F841", "F401"}}).endswith('["F401", "F841"]\n')
    assert COMMIT_SUBJECT.match("fix(term): keep the hook") and not COMMIT_SUBJECT.match(
        "Fix the hook"
    )
    assert README_MARK.search("a\n<!-- test windows -->\n") and not README_MARK.search(
        "x <!-- test -->"
    )
    assert SETUP_HEADING.search("## Quick install") and not SETUP_HEADING.search("## License")
    assert MIT.format(years="2026", owner=OWNER).startswith(
        "MIT License\n\nCopyright (c) 2026 Isaac Yong"
    )
    flagged = {
        "# 1. load the file\nx = 1\n": [("step", 1)],
        "x = 1  # Step 2: parse\n": [("step", 1)],
        "# 3) parse\n": [("step", 1)],
        "# ---- Load ----\n": [("banner", 1)],
        "# ========\n": [("banner", 1)],
        "######\n": [("banner", 1)],
        "# === Load ===\n": [("banner", 1)],
        "# Note: this is slow\n": [("emphasis", 1)],
        "# IMPORTANT: keep the order\n": [("emphasis", 1)],
        "# 파일을 읽는다\n": [("korean", 1)],
        "def f():\n    '''읽는다.'''\n": [("korean", 2)],
        "x = 1\n# 1. 읽기\n": [("step", 2), ("korean", 2)],
    }
    for source, want in flagged.items():
        assert comment_hits(".py", source) == want, (source, comment_hits(".py", source))
    clean = (
        "# 1.5 times the width, because the detector tilts\n",
        "# the step size is fixed by the motor\n",
        "# a note on rounding: half to even\n",
        "x = 'http://example.com'  # noqa: E501\n",
        "#!/usr/bin/env python\n",
        "# ruff: noqa: F401\n",
        "def f():\n    '''Read the file.'''\n",
        "s = '파일'\n",
    )
    for source in clean:
        assert comment_hits(".py", source) == [], (source, comment_hits(".py", source))
    # a source this Python cannot parse still has its comments checked, and is marked unparsed
    assert comment_hits(".py", "def broken(:\n") == [("unparsed", 1)]
    broken = comment_hits(".py", "x = 1\n# 1. 읽기\ny = = 2\n")
    assert sorted(broken) == sorted([("step", 2), ("korean", 2), ("unparsed", 1)]), broken
    assert comment_hits(".py", "# 파일을 읽는다\n", korean_allowed=True) == []
    assert comment_hits(".py", "# 1. 읽기\n", korean_allowed=True) == [("step", 1)]
    assert comment_hits(".rs", "let x = 1; // Step 1: init\n") == [("step", 1)]
    assert comment_hits(".rs", "/// 읽는다\nfn f() {}\n") == [("korean", 1)]
    assert comment_hits(".js", "/* 읽는다 */\nlet a = 'http://x';\n") == [("korean", 1)]
    assert comment_hits(".ps1", "# ---- setup ----\n") == [("banner", 1)]
    assert comment_hits(".sh", "echo $#\n") == [] and comment_hits(".js", "a = 'x//y';\n") == []
    assert KOREAN_SUBJECT == {"hangeul", "hanfix"}

    # A repo that gets its first workflow from fix_lint gets dependabot.yml in the same commit,
    # because a repo with a workflow is checked for one. ruff itself is stubbed out.
    class Done:
        returncode = 0

    def fake_run(*args, **kwargs):
        return "ruff 0.17.1" if args[-1] == "--version" else "[]"

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        real_run, real_sub = globals()["run"], subprocess.run
        globals()["run"], subprocess.run = fake_run, lambda *a, **k: Done()
        try:
            subject = fix_lint(repo)
        finally:
            globals()["run"], subprocess.run = real_run, real_sub
        assert subject and "dependabot" in subject, subject
        assert (repo / ".github" / "workflows" / "lint.yml").exists()
        assert (repo / ".github" / "dependabot.yml").read_text(encoding="utf-8") == DEPENDABOT
        # one that already has its own dependabot.yml keeps it
        (repo / ".github" / "workflows" / "lint.yml").unlink()
        (repo / ".github" / "dependabot.yml").write_text("version: 2\n# mine\n", encoding="utf-8")
        globals()["run"], subprocess.run = fake_run, lambda *a, **k: Done()
        try:
            subject = fix_lint(repo)
        finally:
            globals()["run"], subprocess.run = real_run, real_sub
        assert subject == "ci: run ruff on every push", subject
        assert (
            (repo / ".github" / "dependabot.yml").read_text(encoding="utf-8").endswith("# mine\n")
        )


def main(argv):
    if argv[:1] == ["selftest"]:
        return selftest()
    mode, wanted = (argv[0] if argv else "check"), set(argv[1:])
    sys.stdout.reconfigure(encoding="utf-8")
    listing = json.loads(
        run(
            "gh",
            "repo",
            "list",
            "NaNIsaac",
            "--limit",
            "200",
            "--json",
            "name,visibility,description,repositoryTopics,defaultBranchRef,isFork,isArchived",
        )
    )
    total = 0
    for info in sorted(listing, key=lambda i: i["name"]):
        name = info["name"]
        if (
            info["isArchived"]
            or (info["isFork"] and name not in OWN_FORKS)
            or (wanted and name not in wanted)
        ):
            continue
        repo = ROOT / LOCAL_PATHS.get(name, name)
        if not (repo / ".git").exists():
            continue
        found = list(findings(repo, info))
        for message, _ in found:
            print(f"{name}: {message}")
        total += len(found)
        if mode != "fix":
            continue
        if not any(f for _, f in found):
            continue
        work = fix_worktree(repo, name)
        if work is None:
            continue
        # Each fix is bound to the path it was found in, so find them again in the worktree.
        fixes = [f for _, f in findings(work, info) if f]
        committed = 0
        for fix in fixes:
            before = set(git(work, "status", "--porcelain", "-uall").splitlines())
            subject = fix()
            if subject is None:
                continue
            after = set(git(work, "status", "--porcelain", "-uall").splitlines()) - before
            touched = [line[3:].split(" -> ")[-1].strip('"') for line in after]
            if any(
                not re.match(
                    r"(LICENSE|pyproject\.toml|\.gitattributes|\.mailmap|README[^/]*\.md|\.github/(workflows/|dependabot\.yml))",
                    p,
                )
                for p in touched
            ):
                # eol normalization rewrote other files; undo, a tree this noisy needs a manual look
                git(work, "reset", "-q", "--hard")
                git(work, "clean", "-fdq", "--", "LICENSE", ".gitattributes", ".mailmap")
                print(f"{name}: '{subject}' touched other files, reverted")
                continue
            git(work, "add", "--", *touched)
            git(work, "commit", "-q", "-m", subject)
            committed += 1
            print(f"{name}: committed '{subject}'")
        if committed:
            branch = git(work, "branch", "--show-current").strip()
            print(f"{name}: {committed} commit(s) on {branch} in {work}; push it and open a PR")
        else:
            drop_worktree(repo, work)
    print(f"{total} finding(s)")
    return 1 if total and mode == "check" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
