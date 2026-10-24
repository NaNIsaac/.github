# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml"]
# ///
"""Run a repo's CI locally and print the record BACKLOG rule 26 asks for.

    uv run local_ci.py <repo-name-or-path> [--comment] [--all-matrix]

Every `run:` step of the repo's `.github/workflows/*.yml` runs in order in the repo folder, and a
call to the shared README workflow runs `readme-test.ps1` on README.md and README.ko.md. The result
is one line, `local-ci: <head sha> | ruff ok | pytest N passed | readme ok`; a step that is not in
the repo's CI shows as `n/a`, a failing one as `FAIL` with its output on stderr and exit code 1.
`--comment` posts the line on the open PR whose head is this commit, and refuses when the folder is
dirty or the PR head is a different commit. Matrix jobs run their first value unless `--all-matrix`
is given. Steps using other `${{ }}` expressions, and `uses:` actions other than the README
workflow, are skipped and listed.
"""

import argparse
import itertools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
README_WORKFLOW = re.compile(r"readme\.yml@")
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
KINDS = ("ruff", "pytest", "readme")


@dataclass
class Step:
    kind: str
    name: str
    command: str
    shell: str
    cwd: str
    env: dict


@dataclass
class Result:
    kind: str
    name: str
    ok: bool
    output: str
    seconds: float


def classify(command):
    """The record column a command belongs to: ruff, pytest, or its own first word."""
    if re.search(r"\bruff\b", command):
        return "ruff"
    if "pytest" in command:
        return "pytest"
    if "selftest" in command:
        return "selftest"
    words = command.split()
    return words[0] if words else "step"


def matrix_combos(strategy, every):
    matrix = {k: v for k, v in (strategy or {}).get("matrix", {}).items() if isinstance(v, list)}
    if not matrix:
        return [{}]
    keys = list(matrix)
    combos = [
        dict(zip(keys, values, strict=True)) for values in itertools.product(*matrix.values())
    ]
    return combos if every else combos[:1]


def substitute(text, combo):
    """The text with `matrix.<key>` expressions filled in, or None when another expression is left."""

    def fill(match):
        key = match[1].removeprefix("matrix.")
        return str(combo[key]) if match[1].startswith("matrix.") and key in combo else match[0]

    filled = EXPRESSION.sub(fill, str(text))
    return None if EXPRESSION.search(filled) else filled


def called_only(document):
    """Whether a workflow is triggered only by workflow_call, which makes it a library for others."""
    triggers = document.get("on", document.get(True))  # YAML reads a bare `on` as true
    names = [triggers] if isinstance(triggers, str) else list(triggers or [])
    return names == ["workflow_call"]


def workflow_steps(repo, every=False):
    """(steps, skipped) from the workflows of a repo; skipped holds a reason per step left out."""
    steps, skipped = [], []
    for path in sorted((repo / ".github" / "workflows").glob("*.y*ml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if called_only(document):
            skipped.append(f"{path.name}: reusable workflow, not this repo's CI")
            continue
        for job_id, job in (document.get("jobs") or {}).items():
            if "uses" in job:
                if README_WORKFLOW.search(job["uses"]):
                    steps.append(Step("readme", f"{path.name}:{job_id}", "", "", ".", {}))
                else:
                    skipped.append(f"{path.name}:{job_id}: reusable workflow {job['uses']}")
                continue
            defaults = (job.get("defaults") or {}).get("run") or {}
            for combo in matrix_combos(job.get("strategy"), every):
                label = ",".join(f"{k}={v}" for k, v in combo.items())
                for index, step in enumerate(job.get("steps") or [], 1):
                    if "run" not in step:
                        continue
                    where = f"{path.name}:{job_id}[{index}]" + (f" ({label})" if label else "")
                    env = {**(job.get("env") or {}), **(step.get("env") or {})}
                    values = [
                        step["run"],
                        step.get("working-directory", defaults.get("working-directory", ".")),
                    ]
                    values += list(env.values())
                    filled = [substitute(v, combo) for v in values]
                    if step.get("if") or None in filled:
                        skipped.append(
                            f"{where}: "
                            + ("has an if:" if step.get("if") else "uses a ${{ }} expression")
                        )
                        continue
                    env = dict(zip(env, filled[2:], strict=True))
                    shell = step.get("shell", defaults.get("shell", "bash"))
                    steps.append(Step(classify(filled[0]), where, filled[0], shell, filled[1], env))
    return steps, skipped


def git_bin():
    """Folder with the `sh` that comes with Git for Windows, when sh is not already on PATH."""
    git = shutil.which("git")
    return None if shutil.which("sh") or not git else str(Path(git).resolve().parents[1] / "bin")


def step_environment(step):
    # `uv run` points VIRTUAL_ENV at this script's own environment, which would make a step's
    # `uv pip install` land there instead of in the repo's .venv.
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "PYTHONHOME"}}
    env |= {"PYTHONUTF8": "1", **step.env}
    if (extra := git_bin()) and Path(extra).is_dir():
        key = next((k for k in env if k.upper() == "PATH"), "PATH")
        env[key] = extra + os.pathsep + env.get(key, "")
    return env


def executable(name, env):
    """The full path of a program, looked up in the PATH the step will run with."""
    key = next((k for k in env if k.upper() == "PATH"), "PATH")
    return shutil.which(name, path=env.get(key)) or name


def run_step(repo, step, timeout):
    env = step_environment(step)
    started = time.monotonic()
    if step.kind == "readme":
        readmes = [r for r in ("README.md", "README.ko.md") if (repo / r).exists()]
        outputs, ok = [], True
        for readme in readmes:
            script = Path(__file__).with_name("readme-test.ps1")
            done = subprocess.run(
                [
                    executable("pwsh", env),
                    "-NoProfile",
                    "-File",
                    str(script),
                    "-Readme",
                    readme,
                    "-Force",
                ],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            outputs.append(done.stdout + done.stderr)
            ok = ok and done.returncode == 0
        return Result("readme", step.name, ok, "\n".join(outputs), time.monotonic() - started)
    powershell = step.shell.startswith(("pwsh", "powershell"))
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / ("step.ps1" if powershell else "step.sh")
        script.write_text(step.command, encoding="utf-8")
        argv = (
            [executable("pwsh", env), "-NoProfile", "-File", str(script)]
            if powershell
            else [executable("sh", env), "-e", str(script)]
        )
        try:
            done = subprocess.run(
                argv,
                cwd=repo / step.cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            ok, output = done.returncode == 0, done.stdout + done.stderr
        except subprocess.TimeoutExpired:
            ok, output = False, f"timed out after {timeout} s"
        except FileNotFoundError as error:
            ok, output = False, f"cannot start {argv[0]}: {error}"
    return Result(step.kind, step.name, ok, output, time.monotonic() - started)


def passed_count(output):
    counts = re.findall(r"(\d+) passed", output)
    return int(counts[-1]) if counts else 0


def summary(sha, results):
    """The record line: ruff, pytest and readme columns first, then any other kind that ran."""
    columns = []
    for kind in (*KINDS, *sorted({r.kind for r in results} - set(KINDS))):
        mine = [r for r in results if r.kind == kind]
        if not mine:
            columns.append(f"{kind} n/a" if kind in KINDS else "")
        elif not all(r.ok for r in mine):
            columns.append(f"{kind} FAIL")
        elif kind == "pytest":
            columns.append(f"pytest {sum(passed_count(r.output) for r in mine)} passed")
        else:
            columns.append(f"{kind} ok")
    return f"local-ci: {sha} | " + " | ".join(c for c in columns if c)


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False
    ).stdout.strip()


def comment(repo, sha, line):
    """Post the line on the open PR whose head is sha; return an error message or None."""
    if git(repo, "status", "--porcelain"):
        return "the folder has uncommitted changes, so the record would not describe the commit"
    slug = re.search(r"github\.com[:/](.+?)(\.git)?$", git(repo, "remote", "get-url", "origin"))
    branch = git(repo, "branch", "--show-current")
    if not slug or not branch:
        return "needs a checked-out branch and a GitHub origin"
    found = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "-R",
            slug[1],
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,headRefOid",
            "-q",
            '.[]|"\\(.number) \\(.headRefOid)"',
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    ).stdout.split()
    if len(found) != 2:
        return f"no single open PR with head branch {branch}"
    if found[1] != sha:
        return f"PR #{found[0]} head is {found[1][:12]}, not {sha[:12]}; push or pull first"
    subprocess.run(["gh", "pr", "comment", found[0], "-R", slug[1], "--body", line], check=True)
    return None


def main(argv):
    if argv[:1] == ["selftest"]:
        return selftest()
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "repo", help="folder name under the workspace, or a path (a worktree works)"
    )
    parser.add_argument("--comment", action="store_true")
    parser.add_argument("--all-matrix", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds per step")
    args = parser.parse_args(argv)
    repo = Path(args.repo) if Path(args.repo).is_dir() else ROOT / args.repo
    repo = repo.resolve()
    if not (repo / ".git").exists():
        parser.error(f"{repo} is not a git checkout")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sha = git(repo, "rev-parse", "HEAD")
    steps, skipped = workflow_steps(repo, args.all_matrix)
    for reason in skipped:
        print(f"skipped {reason}", file=sys.stderr)
    results = []
    for step in steps:
        result = run_step(repo, step, args.timeout)
        results.append(result)
        print(
            f"{'ok  ' if result.ok else 'FAIL'} {step.name} ({result.seconds:.0f} s)",
            file=sys.stderr,
        )
        if not result.ok:
            print("\n".join(result.output.splitlines()[-40:]), file=sys.stderr)
    line = summary(sha, results)
    print(line)
    failed = any(not r.ok for r in results)
    if args.comment and not failed and (error := comment(repo, sha, line)):
        print(f"not commented: {error}", file=sys.stderr)
        return 1
    return 1 if failed else 0


def selftest():
    sha = "a" * 40
    ok, bad = (
        (lambda k, out="": Result(k, "x", True, out, 0)),
        (lambda k: Result(k, "x", False, "", 0)),
    )
    assert classify("uvx ruff@0.17.1 check") == "ruff" and classify("uv run pytest -q") == "pytest"
    assert classify("cargo test") == "cargo" and classify("") == "step"
    assert classify("uv run tools/x.py selftest") == "selftest"
    assert called_only({True: {"workflow_call": None}}) and called_only({"on": "workflow_call"})
    assert not called_only({True: {"push": None, "workflow_call": None}})
    assert not called_only({True: ["push"]}) and not called_only({})
    assert passed_count("..\n11 passed, 8 warnings in 57s") == 11 and passed_count("no tests") == 0
    assert summary(sha, [ok("ruff"), ok("pytest", "9 passed in 1s"), ok("readme")]) == (
        f"local-ci: {sha} | ruff ok | pytest 9 passed | readme ok"
    )
    assert summary(sha, []) == f"local-ci: {sha} | ruff n/a | pytest n/a | readme n/a"
    assert summary(sha, [ok("ruff"), bad("pytest")]) == (
        f"local-ci: {sha} | ruff ok | pytest FAIL | readme n/a"
    )
    assert summary(sha, [ok("cargo")]).endswith("| readme n/a | cargo ok")
    assert substitute("py ${{ matrix.python }}", {"python": "3.14"}) == "py 3.14"
    assert substitute("${{ secrets.X }}", {}) is None and substitute("plain", {}) == "plain"
    assert matrix_combos({"matrix": {"py": ["3.10", "3.14"], "os": ["a", "b"]}}, False) == [
        {"py": "3.10", "os": "a"}
    ]
    assert len(matrix_combos({"matrix": {"py": ["3.10", "3.14"], "os": ["a", "b"]}}, True)) == 4
    assert matrix_combos(None, True) == [{}]
    workflow = """
name: ci
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: uvx ruff@0.17.1 check
      - run: echo ${{ secrets.TOKEN }}
      - if: runner.os == 'Linux'
        run: echo linux
  tests:
    strategy:
      matrix:
        python: ["3.10", "3.14"]
    steps:
      - run: echo pytest ${{ matrix.python }}, 4 passed
        working-directory: sub
        env:
          PYV: ${{ matrix.python }}
  readme:
    uses: NaNIsaac/.github/.github/workflows/readme.yml@main
  other:
    uses: someone/else/.github/workflows/x.yml@v1
"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
        steps, skipped = workflow_steps(repo)
        assert [s.kind for s in steps] == ["ruff", "pytest", "readme"], steps
        assert steps[1].command == "echo pytest 3.10, 4 passed" and steps[1].cwd == "sub"
        assert steps[1].env == {"PYV": "3.10"}
        assert len(skipped) == 3 and "reusable workflow someone/else" in skipped[2], skipped
        assert len(workflow_steps(repo, every=True)[0]) == 4
        (repo / "sub").mkdir()
        if shutil.which("sh") or git_bin():  # Git for Windows ships sh
            result = run_step(repo, steps[1], 60)
            assert result.ok and passed_count(result.output) == 4, result
            failing = Step("pytest", "f", "echo broken; exit 3", "bash", ".", {})
            assert not run_step(repo, failing, 60).ok
    print("selftest ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
