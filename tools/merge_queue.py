"""The merge digest for Isaac: every open PR in the NaNIsaac account, by repo, with a risk tier.

    python merge_queue.py                    # print the digest
    python merge_queue.py --write PATH       # refresh the marked block of PATH, keep the rest
    python merge_queue.py selftest

It never merges. The tier is computed from which files a PR touches and how, never from
what its author says, so an agent cannot argue a PR into a lower tier:

    low      README/docs Markdown, added tests, new workflow files: skim and merge
    medium   lock files, changed or removed workflow steps, removed test lines,
             pyproject, scripts and examples, anything not recognised
    physics  code anywhere outside tests, examples, scripts, benchmarks and tools (src/,
             or a script at the root such as sim.py): read the diff

When a file fits no rule it lands in a higher tier, never a lower one. Only low-tier PRs that
are green, mergeable, not stale and not stacked on an open PR get a printed
`gh pr merge --rebase` command, and that is all the script ever does with them.

A PR is stale when its base branch moved after its last check run finished: its CI did not
see what it would be merged into. Two PRs of one repo that touch the same file are listed as
overlapping, so the second is rebased and rechecked after the first is merged.

GitHub closes an issue on merge only when the PR is linked to it and merges into the default
branch. A `Fixes #N` in the body of a PR stacked on another PR's branch is not linked, and a
link made earlier does not close anything while the base is not the default branch. The
digest flags both and lists the issues that may stay open after their fix is merged.
"""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

OWNER = "NaNIsaac"
BOARD = Path(
    os.environ.get("HERDR_BOARD_DIR")
    or Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "herdr-board"
)
SNAPSHOT = BOARD / "prs.json"
CACHE = BOARD / "merge-queue-cache.json"
# prs.py keeps the snapshot fresh to five minutes for its readers; this tool runs every ten, so
# anything older than half an hour means nobody refreshed it and gh is asked as before.
SNAPSHOT_MAX_AGE = 1800
TIERS = ("low", "medium", "physics")
DOC = re.compile(
    r"(^|/)(README[^/]*|CHANGELOG[^/]*|CITATION\.cff|LICENSE[^/]*)$|^docs/.*\.md$", re.IGNORECASE
)
TEST = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$")
WORKFLOW = re.compile(r"^\.github/workflows/")
SOURCE = re.compile(r"^src/")
# Code that is not a test or a helper can change results, wherever it sits (sim.py at the
# root of a research repo is as much physics as src/).
CODE = re.compile(r"\.(py|pyx|rs|c|cc|cpp|h|hpp|cu|cuh|js|ts|jl|m|f90)$", re.IGNORECASE)
HELPER = re.compile(r"^(examples|scripts|benchmarks|tools|notebooks)/")
PRE_MERGE = re.compile(r"^\s*pre-merge:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
# GitHub's closing keywords; each issue needs its own keyword ("Fixes #1, fixes #2").
CLOSING = re.compile(
    rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+(?:{OWNER}/([\w.-]+))?#(\d+)\b",
    re.IGNORECASE,
)


@dataclass
class FileChange:
    path: str
    status: str  # added, modified, removed, renamed
    deleted: int = 0  # lines the PR removes from this file


def file_tier(change: FileChange) -> str:
    """The tier one changed file puts a PR in."""
    path = change.path
    if DOC.search(path):
        return "low"
    if TEST.search(path):
        # Adding tests is safe; deleting assertions weakens what CI checks.
        return "low" if not change.deleted else "medium"
    if WORKFLOW.search(path):
        return "low" if change.status == "added" else "medium"
    if SOURCE.search(path) or (CODE.search(path) and not HELPER.search(path)):
        return "physics"
    return "medium"


def pr_tier(changes: list[FileChange]) -> str:
    if not changes:
        return "medium"
    return max((file_tier(c) for c in changes), key=TIERS.index)


@dataclass
class PullRequest:
    repo: str
    number: int
    title: str
    url: str
    base: str
    head: str
    tier: str
    lines: int
    paths: list[str]
    checks: str  # green, red, pending, none
    mergeable: bool
    stale: bool
    stacked_on: int | None = None
    pre_merge: list[str] = field(default_factory=list)
    overlaps: list[int] = field(default_factory=list)
    default: str = "main"
    fixes: list[int] = field(default_factory=list)  # closing keywords in the body
    linked: list[int] = field(default_factory=list)  # what GitHub will close on merge


def body_fixes(repo: str, body: str) -> list[int]:
    """Issues of `repo` that the body names with a closing keyword."""
    found = (int(n) for r, n in CLOSING.findall(body) if not r or r.lower() == repo.lower())
    return sorted(set(found))


def left_open(p: PullRequest) -> list[int]:
    """Issues the PR says it fixes that its own merge will not close."""
    if p.base != p.default:
        return sorted(set(p.fixes) | set(p.linked))
    return [n for n in p.fixes if n not in p.linked]


class GhError(Exception):
    """A gh call that failed, with the command and what gh said on stderr."""


def gh(*args):
    done = subprocess.run(
        ["gh", *args], check=False, capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode:
        reason = (done.stderr or done.stdout).strip()[-400:] or "no output"
        message = f"gh {' '.join(args[:4])} exited {done.returncode}: {reason}"
        print(message, file=sys.stderr)
        raise GhError(message)
    return done.stdout


def check_state(rollup) -> tuple[str, datetime | None]:
    if not rollup:
        return "none", None
    states = {(c.get("conclusion") or c.get("status") or "").upper() for c in rollup}
    finished = [c.get("completedAt") for c in rollup if c.get("completedAt")]
    last = max((datetime.fromisoformat(t) for t in finished), default=None)
    if states & {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
        return "red", last
    if states & {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", ""}:
        return "pending", last
    return "green", last


def base_moved_at(repo: str, base: str, cache: dict) -> datetime | None:
    key = (repo, base)
    if key not in cache:
        try:
            date = gh(
                "api", f"repos/{OWNER}/{repo}/commits/{base}", "--jq", ".commit.committer.date"
            ).strip()
            cache[key] = datetime.fromisoformat(date)
        except GhError:
            cache[key] = None
    return cache[key]


PR_FIELDS = (
    "number,title,url,baseRefName,headRefName,files,statusCheckRollup,mergeable,comments,"
    "body,closingIssuesReferences"
)


def file_change(f: dict) -> FileChange:
    """One entry of `gh ... --json files`. Only whether a test file lost lines matters, so the
    deleted-line count is enough and no diff has to be fetched."""
    return FileChange(
        f["path"], "added" if f["changeType"] == "ADDED" else "modified", f["deletions"]
    )


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_snapshot(now: datetime | None = None) -> dict[str, list[dict]] | None:
    """The open PRs of OWNER from prs.py's snapshot, by repository name; None when the snapshot
    is missing, unreadable, empty or older than SNAPSHOT_MAX_AGE."""
    data = read_json(SNAPSHOT)
    if not isinstance(data, dict):
        return None
    try:
        age = (
            (now or datetime.now(UTC)) - datetime.fromisoformat(data["fetched_at"])
        ).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    if age > SNAPSHOT_MAX_AGE:
        return None
    by_repo: dict[str, list[dict]] = {}
    for row in data.get("prs") or []:
        owner, _, name = str(row.get("repo", "")).partition("/")
        if owner == OWNER and name:
            by_repo.setdefault(name, []).append(row)
    return by_repo or None


def detail_key(row: dict) -> list:
    """What must be unchanged for a cached `gh pr list` entry to still describe the PR: any push,
    edit or comment moves `updated`, and a check run finishing changes `checks`."""
    return [row.get("head_sha"), row.get("updated"), row.get("checks")]


def search_repos() -> list[str]:
    found = json.loads(
        gh(
            "search",
            "prs",
            "--owner",
            OWNER,
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "repository",
        )
    )
    return sorted({item["repository"]["name"] for item in found})


def list_repo(repo: str) -> list[dict]:
    return json.loads(
        gh(
            "pr",
            "list",
            "-R",
            f"{OWNER}/{repo}",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            PR_FIELDS,
        )
    )


def pr_views(repo: str, rows: list[dict], cache: dict, fresh: dict, failed: dict) -> list[dict]:
    """The `gh pr` JSON of every open PR of one repository, from the snapshot's rows. A PR whose
    (head sha, updated, checks) match the last run is taken from the cache; only the others are
    fetched, one `gh pr view` each, so the heavy fields (files, comments, body) are read for the
    PRs that changed. The snapshot's mergeable, newer than any cached one, replaces UNKNOWN. A PR
    gh cannot read is recorded in `failed` and left out."""
    views = []
    for row in rows:
        key = f"{repo}#{row['number']}"
        hit = cache.get(key)
        if hit and hit["key"] == detail_key(row):
            view = hit["view"]
        else:
            try:
                view = json.loads(
                    gh(
                        "pr",
                        "view",
                        str(row["number"]),
                        "-R",
                        f"{OWNER}/{repo}",
                        "--json",
                        PR_FIELDS,
                    )
                )
            except GhError as exc:
                failed[key] = str(exc)
                continue
        fresh[key] = {"key": detail_key(row), "view": view}
        views.append(view | {"mergeable": row.get("mergeable") or view["mergeable"]})
    return views


def collect(failed: dict | None = None) -> list[PullRequest]:
    """Every open PR of the account. From a fresh snapshot (prs.py) the repositories come
    without a search and only the PRs that changed since the last run are fetched; without one
    it is one search and one `gh pr list` per repository. A repository or PR gh cannot read is
    recorded in `failed` (name -> reason) and the rest is still collected. An empty result
    stops the run, so a failed search cannot be written out as "no open PRs"."""
    failed = {} if failed is None else failed
    snapshot = load_snapshot()
    cache = (read_json(CACHE) or {}) if snapshot else {}
    fresh: dict = {}
    repos = sorted(snapshot) if snapshot else search_repos()
    if not repos:
        raise SystemExit("merge_queue: the PR search returned nothing; nothing was written")
    defaults = {
        r["name"]: r["defaultBranchRef"]["name"]
        for r in json.loads(
            gh("repo", "list", OWNER, "--limit", "200", "--json", "name,defaultBranchRef")
        )
        if r["defaultBranchRef"]
    }
    prs, base_cache = [], {}
    for repo in repos:
        try:
            listing = (
                pr_views(repo, snapshot[repo], cache, fresh, failed)
                if snapshot
                else list_repo(repo)
            )
        except GhError as exc:
            failed[repo] = str(exc)
            continue
        for view in listing:
            number = view["number"]
            if view["mergeable"] == "UNKNOWN":  # GitHub has not computed it yet; asking again does
                try:
                    view["mergeable"] = json.loads(
                        gh(
                            "pr",
                            "view",
                            str(number),
                            "-R",
                            f"{OWNER}/{repo}",
                            "--json",
                            "mergeable",
                        )
                    )["mergeable"]
                except GhError as exc:
                    failed[f"{repo}#{number}"] = str(exc)
            changes = [file_change(f) for f in view["files"]]
            checks, last_check = check_state(view["statusCheckRollup"])
            moved = base_moved_at(repo, view["baseRefName"], base_cache)
            notes = [m.strip() for c in view["comments"] for m in PRE_MERGE.findall(c["body"])]
            prs.append(
                PullRequest(
                    repo=repo,
                    number=number,
                    title=view["title"],
                    url=view["url"],
                    base=view["baseRefName"],
                    head=view["headRefName"],
                    tier=pr_tier(changes),
                    lines=sum(f["additions"] + f["deletions"] for f in view["files"]),
                    paths=[f["path"] for f in view["files"]],
                    checks=checks,
                    mergeable=view["mergeable"] == "MERGEABLE",
                    stale=bool(last_check and moved and moved > last_check),
                    pre_merge=notes,
                    default=defaults.get(repo, "main"),
                    fixes=body_fixes(repo, view["body"] or ""),
                    linked=sorted(i["number"] for i in view["closingIssuesReferences"]),
                )
            )
    link(prs)
    if snapshot:
        BOARD.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(fresh), encoding="utf-8")
        tmp.replace(CACHE)
    return prs


def link(prs: list[PullRequest]) -> None:
    """Stacked PRs (base is another open PR's branch) and same-file overlaps within a repo."""
    by_head = {(p.repo, p.head): p.number for p in prs}
    for p in prs:
        p.stacked_on = by_head.get((p.repo, p.base))
        p.overlaps = sorted(
            q.number
            for q in prs
            if q.repo == p.repo and q.number != p.number and set(q.paths) & set(p.paths)
        )


def ordered(prs: list[PullRequest]) -> list[PullRequest]:
    """Within one repo: a stacked PR after the one it sits on, then low before high tiers."""
    rank = {p.number: (TIERS.index(p.tier), p.number) for p in prs}

    def depth(p):
        d, seen = 0, set()
        while p.stacked_on and p.stacked_on not in seen:
            seen.add(p.stacked_on)
            d += 1
            p = next((q for q in prs if q.number == p.stacked_on), p)
        return d

    return sorted(prs, key=lambda p: (depth(p), *rank[p.number]))


def ready(p: PullRequest) -> bool:
    return (
        p.tier == "low" and p.checks == "green" and p.mergeable and not p.stale and not p.stacked_on
    )


def digest(prs: list[PullRequest], failed: dict | None = None) -> str:
    out = [
        f"## 저장소별 (자동 생성 {datetime.now():%Y-%m-%d %H:%M}, `.github/tools/merge_queue.py`)",
        "",
    ]
    out.append(
        "등급은 바뀐 파일 경로와 CI 상태에서 기계적으로 계산한다. "
        "맞지 않는 파일은 한 단계 위로 간다."
    )
    out.append("")
    if failed:
        out.append("## 조회 실패 (gh가 읽지 못함, 아래 저장소와 PR은 빠졌거나 불완전하다)")
        out.append("")
        out.extend(f"- {name}: {why}" for name, why in sorted(failed.items()))
        out.append("")
    commands, unclosed = [], []
    # An issue another open PR closes from the default branch is taken care of.
    closed_on_main = {(p.repo, n) for p in prs if p.base == p.default for n in p.linked}
    for repo in sorted({p.repo for p in prs}, key=str.lower):
        group = ordered([p for p in prs if p.repo == repo])
        listed: set[int] = set()
        totals = {t: sum(p.lines for p in group if p.tier == t) for t in TIERS}
        out.append(f"### {repo}  (줄 수: " + ", ".join(f"{t} {totals[t]}" for t in TIERS) + ")")
        for p in group:
            flags = [p.checks]
            if not p.mergeable:
                flags.append("충돌 또는 계산 중")
            if p.stale:
                flags.append("stale: base 가 CI 뒤에 바뀜")
            if p.stacked_on:
                flags.append(f"#{p.stacked_on} 위에 쌓임")
            if p.overlaps:
                flags.append("같은 파일: " + ", ".join(f"#{n}" for n in p.overlaps))
            if left := [n for n in left_open(p) if (repo, n) not in closed_on_main]:
                why = f"base `{p.base}`" if p.base != p.default else "본문의 Fixes가 연결 안 됨"
                flags.append(f"merge해도 안 닫힘 ({why}): " + ", ".join(f"#{n}" for n in left))
                unclosed.extend(f"- {repo}#{n} ← #{p.number} ({why})" for n in left)
            out.append(f"- [{p.tier}] #{p.number} {p.title} ({p.lines}줄) — {'; '.join(flags)}")
            out.extend(f"  - pre-merge: {note}" for note in p.pre_merge)
            # After one of two PRs on the same file is merged, the other has to be rebased
            # and rechecked, so only the first of them gets a command.
            if ready(p) and not set(p.overlaps) & listed:
                listed.add(p.number)
                commands.append(
                    f"gh pr merge {p.number} -R {OWNER}/{repo} --rebase --delete-branch"
                )
        out.append("")
    if unclosed:
        out.append("## merge 뒤에도 열려 있을 수 있는 이슈")
        out.append("")
        out.append(
            "고치는 PR이 기본 branch로 merge되지 않거나 이슈에 연결되지 않았다. 아래 PR이 "
            "`--delete-branch`로 merge되면 GitHub가 쌓인 PR의 base를 옮긴다. 그 PR이 merge된 "
            "뒤에도 이슈가 열려 있으면 손으로 닫는다."
        )
        out.append("")
        out.extend(unclosed)
        out.append("")
    out.append(
        "## low 등급 중 바로 merge 할 수 있는 것 "
        "(Isaac 이 훑어본 뒤 실행, 스크립트는 실행하지 않음)"
    )
    out.append("")
    out.extend(["```", *commands, "```"] if commands else ["(없음)"])
    out.append("")
    return "\n".join(out)


BEGIN, END = "<!-- merge_queue.py:begin -->", "<!-- merge_queue.py:end -->"


def replace_generated(text: str, body: str) -> str:
    """The text with the block between the markers replaced by `body`, or `body` appended
    when there are no markers yet. Everything outside the markers is hand-written (orders
    the script cannot know, decisions for Isaac) and stays as it is, whatever its headings."""
    block = f"{BEGIN}\n{body.rstrip()}\n{END}\n"
    start, end = text.find(BEGIN), text.find(END)
    if start != -1 and end > start:
        return text[:start] + block + text[end + len(END) :].lstrip("\n")
    return text.rstrip() + "\n\n" + block


def write(path: Path, body: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else "# MERGE QUEUE\n"
    path.write_text(replace_generated(text, body), encoding="utf-8")


def selftest_snapshot():
    """collect() from a snapshot: no search, no listing, and the heavy fields only for a PR that
    changed since the last run; a missing or old snapshot falls back to gh; a PR or repository gh
    cannot read is reported and the rest is still collected."""
    global gh, BOARD, SNAPSHOT, CACHE
    saved = gh, BOARD, SNAPSHOT, CACHE
    calls: list[tuple] = []
    broken: set[str] = set()

    def view(number, head="a"):
        return {
            "number": number,
            "title": "t",
            "url": "u",
            "baseRefName": "main",
            "headRefName": head,
            "files": [
                {"path": "README.md", "changeType": "MODIFIED", "additions": 1, "deletions": 0}
            ],
            "statusCheckRollup": [],
            "mergeable": "UNKNOWN",
            "comments": [],
            "body": "",
            "closingIssuesReferences": [],
        }

    def fake_gh(*args):
        calls.append(args)
        if args[:2] == ("search", "prs"):
            return json.dumps([{"repository": {"name": "r"}}])
        if args[:2] == ("repo", "list"):
            return json.dumps([{"name": "r", "defaultBranchRef": {"name": "main"}}])
        if args[:2] == ("pr", "list"):
            return json.dumps([view(1)])
        if args[:2] == ("pr", "view"):
            if broken and args[2] in broken:
                raise GhError(f"gh pr view {args[2]} exited 1: HTTP 502")
            if args[-1] == PR_FIELDS:
                return json.dumps(view(int(args[2])))
            return json.dumps({"mergeable": "MERGEABLE"})
        if args[0] == "api":
            return "2026-01-01T00:00:00+00:00"
        raise AssertionError(args)

    def row(sha="s1", updated="u1", mergeable="MERGEABLE"):
        return {
            "repo": f"{OWNER}/r",
            "number": 1,
            "mergeable": mergeable,
            "checks": None,
            "head_sha": sha,
            "updated": updated,
        }

    def write(rows, at=None):
        stamp = (at or datetime.now(UTC)).isoformat()
        SNAPSHOT.write_text(json.dumps({"fetched_at": stamp, "prs": rows}), encoding="utf-8")

    def heavy():
        return sum(1 for c in calls if c[:2] == ("pr", "view") and c[-1] == PR_FIELDS)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            BOARD = Path(tmp)
            SNAPSHOT, CACHE = BOARD / "prs.json", BOARD / "merge-queue-cache.json"
            gh = fake_gh
            collect()
            assert calls[0][:2] == ("search", "prs"), "no snapshot: the search runs as before"
            assert not CACHE.exists()
            calls.clear()
            write([row()])
            first = collect()
            assert not any(c[:2] in (("search", "prs"), ("pr", "list")) for c in calls)
            assert heavy() == 1 and len(calls) == 3, calls  # repo list, view, base commit date
            assert first[0].mergeable, "the snapshot's mergeable replaces UNKNOWN without a query"
            calls.clear()
            again = collect()
            assert heavy() == 0 and again[0].number == 1, (
                "unchanged PRs are answered from the cache"
            )
            calls.clear()
            write([row(sha="s2")])
            collect()
            assert heavy() == 1, "a new head sha fetches that PR again"
            calls.clear()
            write([row(sha="s2", mergeable="CONFLICTING")])
            assert not collect()[0].mergeable and heavy() == 0, (
                "mergeable always comes from the snapshot"
            )
            calls.clear()
            write([row(sha="s2")], at=datetime.now(UTC).replace(year=2020))
            collect()
            assert calls[0][:2] == ("search", "prs"), "an old snapshot is ignored"
            SNAPSHOT.write_text("{", encoding="utf-8")
            assert load_snapshot() is None
            calls.clear()
            write([row(sha="s9"), {**row(sha="s9"), "number": 2}])
            broken.add("1")
            failed: dict = {}
            got = collect(failed)
            assert [p.number for p in got] == [2] and list(failed) == ["r#1"], (got, failed)
            text = digest(got, failed)
            assert "## 조회 실패" in text and "r#1: gh pr view 1 exited 1: HTTP 502" in text
            assert "#2 t" in text, "the PRs gh could read are still in the digest"
            done = subprocess.CompletedProcess(["gh"], 7, "", "gh said no")
            with (
                mock.patch.object(subprocess, "run", return_value=done),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                try:
                    saved[0]("pr", "list")
                except GhError as exc:
                    assert "exited 7: gh said no" in str(exc)
                else:
                    raise AssertionError("a failing gh was accepted")
    finally:
        gh, BOARD, SNAPSHOT, CACHE = saved


def selftest():
    global gh, SNAPSHOT
    SNAPSHOT = BOARD / "prs.selftest-absent.json"  # the real snapshot must not answer these

    def tier(*changes):
        return pr_tier(
            [FileChange(*c) if isinstance(c, tuple) else FileChange(c, "modified") for c in changes]
        )

    assert tier("README.md") == tier("docs/perf.md") == tier("tests/test_x.py") == "low"
    assert tier((".github/workflows/examples.yml", "added")) == "low"
    assert tier(".github/workflows/ci.yml") == "medium"
    assert tier(("tests/test_x.py", "modified", 1)) == "medium"
    assert tier("uv.lock") == tier("pyproject.toml") == tier("examples/run.py") == "medium"
    assert tier("src/pkg/core.py") == "physics" and tier("README.md", "src/a.py") == "physics"
    assert tier("sim.py") == tier("engine/collide.py") == "physics"
    assert tier("scripts/run.py") == tier("benchmarks/perf.py") == "medium"
    # The invariant: a file the rules do not know never lowers a PR, and adding any file
    # can only keep or raise the tier.
    for path in ("Makefile", "data/x.h5", "src", "notebooks/a.ipynb", "setup.cfg"):
        assert file_tier(FileChange(path, "modified")) != "low", path
    base = [FileChange("README.md", "modified")]
    for extra in ("uv.lock", "src/x.py", "tests/t.py", "foo/bar", ".github/workflows/a.yml"):
        assert TIERS.index(pr_tier([*base, FileChange(extra, "modified")])) >= TIERS.index(
            pr_tier(base)
        )
    f = {"path": "w.yml", "changeType": "ADDED", "deletions": 0}
    assert file_change(f) == FileChange("w.yml", "added", 0)
    g = {"path": "tests/t.py", "changeType": "MODIFIED", "deletions": 3}
    assert (
        file_change(g) == FileChange("tests/t.py", "modified", 3)
        and file_tier(file_change(g)) == "medium"
    )
    a = PullRequest("r", 1, "", "", "main", "x", "low", 1, ["a"], "green", True, False)
    b = PullRequest("r", 2, "", "", "x", "y", "low", 1, ["a"], "green", True, False)
    link([a, b])
    assert b.stacked_on == 1 and a.overlaps == [2] and [p.number for p in ordered([b, a])] == [1, 2]
    assert ready(a) and not ready(b)
    c = PullRequest("r", 3, "", "", "main", "z", "low", 1, ["a"], "green", True, False)
    link([a, c])
    commands = [line for line in digest([a, c]).splitlines() if line.startswith("gh pr merge")]
    assert commands == [f"gh pr merge 1 -R {OWNER}/r --rebase --delete-branch"], commands
    assert body_fixes("r", "Fixes #22. Stacked on #15.") == [22]
    assert body_fixes("r", "fixes #1, closes: #2 and #3; resolved NaNIsaac/r#4") == [1, 2, 4]
    assert body_fixes("r", "Fixes NaNIsaac/other#5, prefix#6, Refs #7") == []
    # Stacked: the body's Fixes is not linked, and nothing closes until the base is main.
    b.fixes, b.linked = [22], []
    assert left_open(b) == [22]
    # A link made while the PR targeted main does not close the issue from another base.
    b.fixes, b.linked = [], [29]
    assert left_open(b) == [29]
    # On main and linked: GitHub closes it. On main but not linked: flagged.
    a.fixes, a.linked = [36], [36]
    assert left_open(a) == []
    a.linked = []
    assert left_open(a) == [36]
    a.fixes = b.fixes = b.linked = []
    b.fixes = [22]
    text = digest([a, b])
    assert "- r#22 ← #2 (base `x`)" in text and "merge해도 안 닫힘" in text
    a.linked = [22]
    assert "r#22" not in digest([a, b])
    a.linked = b.fixes = []
    hand = "# Q\n\n## 0. by hand\nkeep\n\n## 4. also by hand\nkeep too\n"
    once = replace_generated(hand, "## generated\nv1\n")
    twice = replace_generated(once + "\n## 9. added later by hand\nstay\n", "## generated\nv2\n")
    assert "v1" not in twice and "v2" in twice and twice.count(BEGIN) == 1
    assert all(s in twice for s in ("## 0. by hand", "## 4. also by hand", "## 9. added later"))
    # An empty search stops the run, and --write then leaves the file as it was.
    real_gh, gh = gh, lambda *args: "[]"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "MERGE_QUEUE.md"
            target.write_text("# Q\n\nkeep\n", encoding="utf-8")
            try:
                main(["--write", str(target)])
            except SystemExit as stop:
                assert "returned nothing" in str(stop)
            else:
                raise AssertionError("an empty search was accepted")
            assert target.read_text(encoding="utf-8") == "# Q\n\nkeep\n"
    finally:
        gh = real_gh
    selftest_snapshot()
    print("selftest ok")


def main(argv):
    if argv[:1] == ["selftest"]:
        return selftest()
    failed: dict = {}
    try:
        body = digest(collect(failed), failed)
    except GhError as exc:
        raise SystemExit(f"merge_queue: {exc}") from exc
    if argv[:1] == ["--write"] and len(argv) == 2:
        write(Path(argv[1]), body)
        print(f"wrote {argv[1]}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
