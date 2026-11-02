import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from conventions import (
    OVERVIEW_END,
    OVERVIEW_START,
    documentation_findings,
    main,
    overview_block,
    overview_destination,
    overview_links,
    overview_problems,
    overview_readme,
)


class OverviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "QoraGeom"
        (self.repo / "docs").mkdir(parents=True)
        self.source = self.repo / "docs" / "index.md"
        self.source.write_text(
            "# Overview\n\nMeasured values stay unchanged: 2θ = 30°.\n", encoding="utf-8"
        )
        self.readme = self.repo / "README.md"
        self.before = "# QoraGeom\n\nDescription.\n\n"
        self.after = "\n\n## Install\n\n## Quick start\n\n## Documentation\n\n## License\n"
        self.readme.write_text(
            self.before + OVERVIEW_START + "\n" + OVERVIEW_END + self.after,
            encoding="utf-8",
        )

    def test_destinations_keep_schemes_and_rebase_paths_queries_and_anchors(self):
        for source, expected in {
            "install.md": "docs/install.md",
            "./guides/a.md?raw=1#usage": "docs/guides/a.md?raw=1#usage",
            "../README.md#use": "README.md#use",
            "../../other": "../other",
            "#start": "docs/index.md#start",
            "?raw=1": "docs/index.md?raw=1",
            "<media/a b.png>": "<docs/media/a b.png>",
            "https://example.test/a": "https://example.test/a",
            "mailto:owner@example.test": "mailto:owner@example.test",
            "//example.test/a": "//example.test/a",
            "/assets/a.png": "/assets/a.png",
        }.items():
            with self.subTest(source=source):
                self.assertEqual(overview_destination(source), expected)

    def test_markdown_images_nested_destinations_references_and_html(self):
        self.assertEqual(overview_links("[here]()"), "[here](docs/index.md)")
        source = (
            '[Guide](install.md "Install") ![image](media/a(b(c)).png)\n'
            "[![badge](media/badge.svg)](guide.md)\n"
            "[space](<guide with spaces.md>)\n"
            '[guide]: guides/use.md "Title"\n'
            "<a href=\"../LICENSE\"><img src='media/image.png'></a>\n"
        )
        expected = (
            '[Guide](docs/install.md "Install") ![image](docs/media/a(b(c)).png)\n'
            "[![badge](docs/media/badge.svg)](docs/guide.md)\n"
            "[space](<docs/guide with spaces.md>)\n"
            '[guide]: docs/guides/use.md "Title"\n'
            "<a href=\"LICENSE\"><img src='docs/media/image.png'></a>\n"
        )
        self.assertEqual(overview_links(source), expected)

    def test_code_is_preserved_including_long_and_tilde_fences(self):
        source = (
            "`[code](a.md)` ``[code](b.md)`` [text](c.md)\n"
            '````md\n```\n[x](d.md) <img src="a.png">\n`````\n'
            "~~~md\n[x](e.md)\n~~~\n"
            "`multiline\n[code](f.md)`\n"
        )
        self.assertEqual(
            overview_links(source), source.replace("[text](c.md)", "[text](docs/c.md)")
        )

    def test_frontmatter_and_title_are_not_copied_into_the_body(self):
        self.source.write_text("---\ntitle: Overview\n---\n# Overview\n\nBody.\n", encoding="utf-8")
        block = overview_block(self.repo, "QoraGeom")
        self.assertIn(
            "Full documentation: [QoraGeom](https://nanisaac.github.io/docs/qorageom/).", block
        )
        self.assertIn("\n\nBody.\n", block)
        self.assertNotIn("# Overview", block)
        self.assertNotIn("title:", block)

    def test_sync_is_idempotent_and_does_not_manage_other_readme_content(self):
        updated = overview_readme(self.repo, "QoraGeom")
        self.assertTrue(updated.startswith(self.before) and updated.endswith(self.after))
        self.readme.write_text(updated, encoding="utf-8")
        self.assertEqual(overview_problems(self.repo, "QoraGeom"), [])
        self.assertEqual(overview_readme(self.repo, "QoraGeom"), updated)
        self.readme.write_text(
            updated.replace("Description.", "Revised description."), encoding="utf-8"
        )
        self.assertEqual(overview_problems(self.repo, "QoraGeom"), [])
        self.source.write_text("# Overview\n\nChanged source.\n", encoding="utf-8")
        self.assertIn("differs", overview_problems(self.repo, "QoraGeom")[0][1])

    def test_invalid_markers_and_late_blocks_refuse_to_overwrite(self):
        for text in (
            self.before + self.after,
            self.before + OVERVIEW_END + "\n" + OVERVIEW_START,
            self.before + OVERVIEW_START * 2 + OVERVIEW_END,
            self.before + "## Install\n" + OVERVIEW_START + OVERVIEW_END,
        ):
            with self.subTest(text=text):
                self.readme.write_text(text, encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["--sync-overview", str(self.repo)]), 1)
                self.assertEqual(self.readme.read_text(encoding="utf-8"), text)

    def test_cli_sync_and_check_enforce_only_pilots(self):
        with (
            patch("conventions.git", return_value="https://github.com/NaNIsaac/QoraGeom.git\n"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(["--check-overview", str(self.repo)]), 1)
            self.assertEqual(main(["--sync-overview", str(self.repo)]), 0)
            self.assertEqual(main(["--check-overview", str(self.repo)]), 0)
        self.source.write_text("# Overview\n\nChanged.\n", encoding="utf-8")
        with patch("conventions.git", return_value="git@github.com:NaNIsaac/Other.git\n"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--check-overview", str(self.repo)]), 0)
            self.assertTrue(output.getvalue().startswith("warning: Other:"))
        messages = list(documentation_findings(self.repo, "Other"))
        self.assertTrue(any("docs-overview" in message for message, _ in messages))
        self.assertTrue(all(message.startswith("warning:") for message, _ in messages))

    def test_without_docs_is_skipped_and_missing_index_is_reported(self):
        self.source.rename(self.repo / "source.md")
        self.assertTrue(overview_problems(self.repo, "QoraGeom"))
        elsewhere = self.repo / "plain"
        elsewhere.mkdir()
        self.assertEqual(overview_problems(elsewhere, "QoraGeom"), [])


if __name__ == "__main__":
    unittest.main()
