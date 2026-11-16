import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import conventions
from conventions import OWNER, license_findings

INFO = {"name": "Demo", "visibility": "PUBLIC"}
OTHER_HOLDER = "MIT License\n\nCopyright (c) 2024 Someone Else\n"


def apply_all(found):
    for _, fix in found:
        if fix:
            fix()


class LicenseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "Demo"
        self.repo.mkdir()

    def test_without_the_flag_a_missing_license_is_reported_and_nothing_written(self):
        found = list(license_findings(self.repo, INFO))
        self.assertEqual([fix for _, fix in found], [None])
        self.assertIn("report only: the license is the owner's choice", found[0][0])
        self.assertNotIn("--license-for", found[0][0])
        apply_all(found)
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_a_flag_for_another_repo_writes_nothing(self):
        (self.repo / "LICENSE").write_text(OTHER_HOLDER, encoding="utf-8")
        found = list(license_findings(self.repo, INFO, {"Other"}))
        self.assertEqual([fix for _, fix in found], [None])
        apply_all(found)
        self.assertEqual((self.repo / "LICENSE").read_text(encoding="utf-8"), OTHER_HOLDER)
        apply_all(license_findings(self.repo.parent, INFO, {"Other"}))
        self.assertEqual(sorted(p.name for p in self.repo.parent.iterdir()), ["Demo"])

    def test_a_flag_for_this_repo_writes_the_license(self):
        apply_all(license_findings(self.repo, INFO, {"Demo"}))
        text = (self.repo / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn(OWNER, text)

    def test_a_flag_for_this_repo_rewrites_the_holder(self):
        (self.repo / "LICENSE").write_text(OTHER_HOLDER, encoding="utf-8")
        without = list(license_findings(self.repo, INFO))
        self.assertIn("report only", without[0][0])
        apply_all(license_findings(self.repo, INFO, {"Demo"}))
        self.assertIn(f"2024 {OWNER}", (self.repo / "LICENSE").read_text(encoding="utf-8"))

    def test_main_passes_the_named_repos_and_keeps_them_out_of_the_repo_filter(self):
        (self.repo / ".git").mkdir()
        listing = [
            {**INFO, "isArchived": False, "isFork": False, "defaultBranchRef": {"name": "main"}}
        ]
        seen = []

        def fake_findings(repo, info, license_for=frozenset()):
            seen.append((info["name"], set(license_for)))
            return iter(())

        with (
            patch.object(conventions, "ROOT", self.repo.parent),
            patch.object(conventions, "run", return_value=json.dumps(listing)),
            patch.object(conventions, "findings", fake_findings),
            patch.object(conventions, "documentation_findings", lambda repo, name: iter(())),
            contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")),
        ):
            self.assertEqual(conventions.main(["fix", "--license-for", "Demo"]), 0)
            self.assertEqual(conventions.main(["fix", "Demo", "--license-for", "Other"]), 0)
        self.assertEqual(seen, [("Demo", {"Demo"}), ("Demo", {"Other"})])

    def test_license_for_without_a_name_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(conventions.main(["fix", "--license-for"]), 2)


if __name__ == "__main__":
    unittest.main()
