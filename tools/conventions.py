"""Repo conventions across D:\\03_Develop\\NaNIsaac.

    python conventions.py check [repo ...]
    python conventions.py fix   [repo ...]   # commits locally, never pushes

check is read-only. fix handles only the mechanical rules and skips repos with
uncommitted changes. Rules that need judgement are reported, not fixed.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
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
AI_TRAILER = r"co-authored-by:.*(claude|anthropic)|generated with.*claude"

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
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout


def git(repo, *args):
    return run("git", *args, cwd=repo)


def hangul_ratio(text):
    letters = len(re.findall(r"[A-Za-z가-힣]", text))
    return len(re.findall(r"[가-힣]", text)) / letters if letters else 0.0


def copyright_years(repo):
    years = git(repo, "log", "--format=%ad", "--date=format:%Y").split()
    first, now = (min(years) if years else str(date.today().year)), str(date.today().year)
    return first if first == now else f"{first}-{now}"


def fix_license(repo):
    (repo / "LICENSE").write_text(MIT.format(years=copyright_years(repo), owner=OWNER), encoding="utf-8")
    return "docs: add MIT license"


def fix_holder(repo, path):
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"(Copyright \(c\) [\d-]+ ).+", rf"\g<1>{OWNER}", text, count=1), encoding="utf-8")
    return "docs: unify license copyright holder"


def fix_gitattributes(repo):
    path = repo / ".gitattributes"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(old.rstrip("\n") + ("\n" if old else "") + "* text=auto eol=lf\n", encoding="utf-8")
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


def wheel_files(repo, out):
    """Build a wheel into out and return its file names, without the dist-info metadata."""
    done = subprocess.run(["uv", "build", "--wheel", "--out-dir", str(out), str(repo)], capture_output=True, text=True)
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


def fix_uv_build(repo):
    """Switch hatchling/setuptools to uv_build when the wheel stays identical; otherwise leave it."""
    path = repo / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    module = data["project"]["name"].replace("-", "_").lower()
    tool = data.get("tool", {})
    hatch = tool.get("hatch", {})
    wheel_packages = hatch.get("build", {}).get("targets", {}).get("wheel", {}).get("packages", [f"src/{module}"])
    if not (repo / "src" / module / "__init__.py").exists() or "setuptools" in tool:
        print(f"{repo.name}: uv_build skipped, not a plain src/{module} package")
        return None
    if wheel_packages != [f"src/{module}"] or set(hatch) - {"build"}:
        print(f"{repo.name}: uv_build skipped, custom hatch build settings")
        return None
    version = run("uv", "--version").split()[1]
    minor = int(version.split(".")[1])
    new = rewrite_tables(text, {
        "[build-system]": f'[build-system]\nrequires = ["uv_build>={version},<0.{minor + 1}"]\nbuild-backend = "uv_build"\n\n',
        "[tool.hatch.build.targets.wheel]": "",
        "[tool.hatch.build.targets.sdist]": "",
    })
    with tempfile.TemporaryDirectory() as tmp:
        before = wheel_files(repo, Path(tmp) / "before")
        path.write_text(new, encoding="utf-8")
        after = wheel_files(repo, Path(tmp) / "after")
    if before is None or after != before:
        git(repo, "checkout", "--", "pyproject.toml")
        print(f"{repo.name}: uv_build reverted, " + ("current build fails" if before is None else "wheel contents differ"))
        return None
    return "build: switch to uv_build"


def findings(repo, info):
    """Yield (message, fix) pairs; fix is a zero-argument callable or None."""
    name = repo.name

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
    mailmap = (repo / ".mailmap").read_text(encoding="utf-8") if (repo / ".mailmap").exists() else ""
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

    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        backend = re.search(r'build-backend\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"))
        if backend and backend[1] != "uv_build":
            yield f"build backend is {backend[1]}, not uv_build", lambda: fix_uv_build(repo)

    readme = repo / "README.md"
    if not readme.exists():
        yield "no README.md", None
    elif hangul_ratio(readme.read_text(encoding="utf-8", errors="replace")) > 0.15:
        yield "README.md is not English (move to README.ko.md, write README.md in English)", None

    if git(repo, "log", "--all", "-i", "-E", f"--grep={AI_TRAILER}", "--format=%h"):
        yield "AI trailers in history (run deai)", None


def selftest():
    assert hangul_ratio("hello") == 0 and hangul_ratio("한글 abc") > 0.3
    src = "[a]\nx = 1\n\n[b]\ny = 2\n\n[c]\nz = 3\n"
    assert rewrite_tables(src, {"[b]": ""}) == "[a]\nx = 1\n\n[c]\nz = 3\n"
    assert MIT.format(years="2026", owner=OWNER).startswith("MIT License\n\nCopyright (c) 2026 Isaac Yong")


def main(argv):
    if argv[:1] == ["selftest"]:
        return selftest()
    mode, wanted = (argv[0] if argv else "check"), set(argv[1:])
    listing = json.loads(run(
        "gh", "repo", "list", "NaNIsaac", "--limit", "200", "--json",
        "name,visibility,description,repositoryTopics,defaultBranchRef,isFork,isArchived",
    ))
    total = 0
    for info in sorted(listing, key=lambda i: i["name"]):
        name = info["name"]
        if info["isArchived"] or (info["isFork"] and name not in OWN_FORKS) or (wanted and name not in wanted):
            continue
        repo = ROOT / name
        if not (repo / ".git").exists():
            continue
        found = list(findings(repo, info))
        for message, _ in found:
            print(f"{name}: {message}")
        total += len(found)
        if mode != "fix":
            continue
        fixes = [f for _, f in found if f]
        if fixes and git(repo, "status", "--porcelain", "--untracked-files=no").strip():
            print(f"{name}: uncommitted changes, fixes skipped")
            continue
        for fix in fixes:
            before = set(git(repo, "status", "--porcelain").splitlines())
            subject = fix()
            if subject is None:
                continue
            after = set(git(repo, "status", "--porcelain").splitlines()) - before
            touched = [line[3:].split(" -> ")[-1].strip('"') for line in after]
            if any(not re.match(r"(LICENSE|pyproject\.toml|\.gitattributes|\.mailmap|README[^/]*\.md|\.github/workflows/)", p) for p in touched):
                # eol normalization rewrote other files; undo, a tree this noisy needs a manual look
                git(repo, "reset", "-q", "--hard")
                git(repo, "clean", "-fdq", "--", "LICENSE", ".gitattributes", ".mailmap")
                print(f"{name}: '{subject}' touched other files, reverted")
                continue
            git(repo, "add", "--", *touched)
            git(repo, "commit", "-q", "-m", subject)
            print(f"{name}: committed '{subject}'")
    print(f"{total} finding(s)")
    return 1 if total and mode == "check" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
