import json
import tempfile
import unittest
from pathlib import Path

from conventions import documentation_findings, documentation_problems


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.readme = self.repo / "README.md"
        self.readme.write_text(
            "# Package\n\nDescription.\n\n## Install\n\n## Quick start\n\n"
            "## Documentation\n\n## License\n",
            encoding="utf-8",
        )

    def test_structure_order_length_and_fenced_headings(self):
        self.assertEqual(list(documentation_problems(self.repo)), [])
        text = self.readme.read_text(encoding="utf-8")
        self.readme.write_text(text.replace("## Install", "## Usage"), encoding="utf-8")
        self.assertIn("'Install'", next(documentation_problems(self.repo))[1])
        self.readme.write_text(
            text.replace("## Install", "```md\n## Install\n```"), encoding="utf-8"
        )
        self.assertTrue(list(documentation_problems(self.repo)))
        self.readme.write_text(
            "# X\n## License\n## Install\n## Quick start\n## Documentation\n", encoding="utf-8"
        )
        self.assertIn("must follow", next(documentation_problems(self.repo))[1])
        self.readme.write_text(text + "\n" * (200 - len(text.splitlines())), encoding="utf-8")
        self.assertEqual(list(documentation_problems(self.repo)), [])
        self.readme.write_text(self.readme.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(next(documentation_problems(self.repo))[0], "readme-length")

    def test_recursive_navigation_and_invalid_json(self):
        docs = self.repo / "docs"
        (docs / "guides").mkdir(parents=True)
        (docs / "index.md").write_text("# Overview", encoding="utf-8")
        (docs / "guides/use.md").write_text("# Guide", encoding="utf-8")
        nav = docs / "nav.json"
        self.assertIn("missing or invalid", next(documentation_problems(self.repo))[1])
        nav.write_text("{", encoding="utf-8")
        self.assertTrue(list(documentation_problems(self.repo)))
        nav.write_text(json.dumps({"sidebar": [{"link": "index.md#overview"}]}), encoding="utf-8")
        self.assertIn("guides/use.md", next(documentation_problems(self.repo))[1])
        nav.write_text(
            json.dumps(
                {
                    "sidebar": [
                        {"link": "index.md#overview"},
                        {"items": [{"link": "./guides/use.md"}]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(list(documentation_problems(self.repo)), [])

    def test_warning_rollout_and_reasoned_exemptions(self):
        self.readme.write_text("# Package\n", encoding="utf-8")
        self.assertTrue(
            all(m.startswith("warning: ") for m, _ in documentation_findings(self.repo, "other"))
        )
        for name in ("QoraGeom", "QoraPhase"):
            self.assertTrue(
                all(
                    not m.startswith("warning: ")
                    for m, _ in documentation_findings(self.repo, name)
                )
            )
        config = self.repo / ".conventions.toml"
        config.write_text('[exempt]\nreadme-structure = ""\n', encoding="utf-8")
        self.assertTrue(list(documentation_problems(self.repo)))
        config.write_text(
            '[exempt]\nreadme-structure = "No executable package"\n', encoding="utf-8"
        )
        self.assertEqual(list(documentation_problems(self.repo)), [])


if __name__ == "__main__":
    unittest.main()
