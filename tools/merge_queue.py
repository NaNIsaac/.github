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
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

OWNER = "NaNIsaac"
TIERS = ("low", "medium", "physics")
DOC = re.compile(
    r"(^|/)(README[^/]*|CHANGELOG[^/]*|CITATION\.cff|LICENSE[^/]*)$|^docs/.*\.md$", re.I
)
TEST = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$")
WORKFLOW = re.compile(r"^\.github/workflows/")
SOURCE = re.compile(r"^src/")
# Code that is not a test or a helper can change results, wherever it sits (sim.py at the
# root of a research repo is as much physics as src/).
CODE = re.compile(r"\.(py|pyx|rs|c|cc|cpp|h|hpp|cu|cuh|js|ts|jl|m|f90)$", re.I)
HELPER = re.compile(r"^(examples|scripts|benchmarks|tools|notebooks)/")
PRE_MERGE = re.compile(r"^\s*pre-merge:\s*(.+)$", re.M | re.I)


@dataclass
class FileChange:
    path: str
    status: str  # added, modified, removed, renamed
    removed_lines: list[str] = field(default_factory=list)


def file_tier(change: FileChange) -> str:
    """The tier one changed file puts a PR in."""
    path = change.path
    if DOC.search(path):
        return "low"
    if TEST.search(path):
        # Adding tests is safe; deleting assertions weakens what CI checks.
        return "low" if not change.removed_lines else "medium"
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


def gh(*args):
    out = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return out.stdout


def parse_patch(patch: str) -> tuple[dict[str, list[str]], set[str]]:
    """Removed lines per file, and the files the diff creates, from a unified diff."""
    removed: dict[str, list[str]] = {}
    created: set[str] = set()
    current = None
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[-1]
            removed[current] = []
        elif current and line.startswith("new file mode"):
            created.add(current)
        elif current and line.startswith("-") and not line.startswith("---"):
            removed[current].append(line[1:])
    return removed, created


def check_state(rollup) -> tuple[str, datetime | None]:
    if not rollup:
        return "none", None
    states = {(c.get("conclusion") or c.get("status") or "").upper() for c in rollup}
    finished = [c.get("completedAt") for c in rollup if c.get("completedAt")]
    last = max((datetime.fromisoformat(t.replace("Z", "+00:00")) for t in finished), default=None)
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
            cache[key] = datetime.fromisoformat(date.replace("Z", "+00:00"))
        except subprocess.CalledProcessError:
            cache[key] = None
    return cache[key]


def collect() -> list[PullRequest]:
    listing = json.loads(
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
            "repository,number",
        )
    )
    fields = "title,url,baseRefName,headRefName,files,statusCheckRollup,mergeable,comments"
    prs, base_cache = [], {}
    for item in listing:
        repo, number = item["repository"]["name"], item["number"]
        view = json.loads(gh("pr", "view", str(number), "-R", f"{OWNER}/{repo}", "--json", fields))
        removed, created = parse_patch(gh("pr", "diff", str(number), "-R", f"{OWNER}/{repo}"))
        changes = [
            FileChange(
                f["path"],
                "added" if f["path"] in created else "modified",
                removed.get(f["path"], []),
            )
            for f in view["files"]
        ]
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
            )
        )
    link(prs)
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


def digest(prs: list[PullRequest]) -> str:
    out = [
        f"## 저장소별 (자동 생성 {datetime.now():%Y-%m-%d %H:%M}, `.github/tools/merge_queue.py`)",
        "",
    ]
    out.append(
        "등급은 바뀐 파일 경로와 CI 상태에서 기계적으로 계산한다. "
        "맞지 않는 파일은 한 단계 위로 간다."
    )
    out.append("")
    commands = []
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


def selftest():
    def tier(*changes):
        return pr_tier(
            [FileChange(*c) if isinstance(c, tuple) else FileChange(c, "modified") for c in changes]
        )

    assert tier("README.md") == tier("docs/perf.md") == tier("tests/test_x.py") == "low"
    assert tier((".github/workflows/examples.yml", "added")) == "low"
    assert tier(".github/workflows/ci.yml") == "medium"
    assert tier(("tests/test_x.py", "modified", ["    assert x == 1"])) == "medium"
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
    assert parse_patch("diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n-old\n+new\n") == (
        {"t.py": ["old"]},
        set(),
    )
    created = "diff --git a/w.yml b/w.yml\nnew file mode 100644\n--- /dev/null\n+++ b/w.yml\n+x\n"
    assert parse_patch(created) == ({"w.yml": []}, {"w.yml"})
    a = PullRequest("r", 1, "", "", "main", "x", "low", 1, ["a"], "green", True, False)
    b = PullRequest("r", 2, "", "", "x", "y", "low", 1, ["a"], "green", True, False)
    link([a, b])
    assert b.stacked_on == 1 and a.overlaps == [2] and [p.number for p in ordered([b, a])] == [1, 2]
    assert ready(a) and not ready(b)
    c = PullRequest("r", 3, "", "", "main", "z", "low", 1, ["a"], "green", True, False)
    link([a, c])
    commands = [line for line in digest([a, c]).splitlines() if line.startswith("gh pr merge")]
    assert commands == [f"gh pr merge 1 -R {OWNER}/r --rebase --delete-branch"], commands
    hand = "# Q\n\n## 0. by hand\nkeep\n\n## 4. also by hand\nkeep too\n"
    once = replace_generated(hand, "## generated\nv1\n")
    twice = replace_generated(once + "\n## 9. added later by hand\nstay\n", "## generated\nv2\n")
    assert "v1" not in twice and "v2" in twice and twice.count(BEGIN) == 1
    assert all(s in twice for s in ("## 0. by hand", "## 4. also by hand", "## 9. added later"))
    print("selftest ok")


def main(argv):
    if argv[:1] == ["selftest"]:
        return selftest()
    body = digest(collect())
    if argv[:1] == ["--write"] and len(argv) == 2:
        write(Path(argv[1]), body)
        print(f"wrote {argv[1]}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
