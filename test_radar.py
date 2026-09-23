#!/usr/bin/env python3
"""Tests for the parts that read someone else's output.

Everything here is offline. The pieces worth testing are the ones handed
whatever GitHub or git decides to send, not the HTTP plumbing.
"""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import radar


class RepoFromUrl(unittest.TestCase):
    def test_https(self):
        self.assertEqual(
            radar.repo_from_url("https://github.com/rmz-oz/actions-radar.git"),
            "rmz-oz/actions-radar",
        )

    def test_ssh(self):
        self.assertEqual(
            radar.repo_from_url("git@github.com:rmz-oz/actions-radar.git"),
            "rmz-oz/actions-radar",
        )

    def test_ssh_protocol(self):
        self.assertEqual(
            radar.repo_from_url("ssh://git@github.com/rmz-oz/actions-radar"),
            "rmz-oz/actions-radar",
        )

    def test_trailing_slash(self):
        self.assertEqual(
            radar.repo_from_url("https://github.com/rmz-oz/actions-radar/"),
            "rmz-oz/actions-radar",
        )

    def test_enterprise_host(self):
        self.assertEqual(
            radar.repo_from_url("https://git.example.edu/team/tool.git"), "team/tool"
        )

    def test_nonsense(self):
        with self.assertRaises(radar.RadarError):
            radar.repo_from_url("notaurl")


class PickError(unittest.TestCase):
    def test_anchors_on_the_error_not_the_tail(self):
        log = "\n".join(
            ["2026-09-21T19:28:40.000Z step one"]
            + ["2026-09-21T19:28:41.000Z npx wrangler deploy"]
            + ["2026-09-21T19:28:42.000Z ##[error]missing CLOUDFLARE_API_TOKEN"]
            + ["2026-09-21T19:28:43.00%dZ Cleaning up orphan processes" % i for i in range(5)]
        )
        out = radar.pick_error(log, 30)
        self.assertIn("missing CLOUDFLARE_API_TOKEN", out)
        self.assertNotIn("Cleaning up", out)

    def test_keeps_the_commands_before_the_error(self):
        log = "\n".join(
            ["2026-09-21T19:28:4%d.000Z line %d" % (i % 10, i) for i in range(20)]
            + ["2026-09-21T19:28:59.000Z ##[error]boom"]
        )
        out = radar.pick_error(log, 5).splitlines()
        self.assertEqual(len(out), 5)
        self.assertTrue(out[-1].endswith("boom"))

    def test_spans_from_first_error_to_last(self):
        log = "\n".join(
            [
                "2026-09-21T19:28:40.000Z ##[error]first",
                "2026-09-21T19:28:41.000Z middle",
                "2026-09-21T19:28:42.000Z ##[error]last",
                "2026-09-21T19:28:43.000Z cleanup",
            ]
        )
        out = radar.pick_error(log, 2)
        self.assertEqual(out.splitlines(), ["##[error]first", "middle", "##[error]last"])

    def test_falls_back_to_the_tail_without_markers(self):
        log = "\n".join("2026-09-21T19:28:40.000Z line %d" % i for i in range(10))
        self.assertEqual(radar.pick_error(log, 3).splitlines(), ["line 7", "line 8", "line 9"])

    def test_strips_timestamps(self):
        self.assertEqual(
            radar.pick_error("2026-09-21T19:28:43.1327292Z hello", 5), "hello"
        )

    def test_empty_log(self):
        self.assertEqual(radar.pick_error("", 5), "")
        self.assertEqual(radar.pick_error("\n\n  \n", 5), "")


class Unzip(unittest.TestCase):
    def test_reads_the_last_text_member(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("1_setup.txt", "setup")
            zf.writestr("2_deploy.txt", "deploy failed")
        self.assertEqual(radar.unzip(buf.getvalue()), b"deploy failed")

    def test_survives_a_broken_archive(self):
        self.assertEqual(radar.unzip(b"PKnot really a zip"), b"")


class Report(unittest.TestCase):
    def run_written(self, found, repos=("a/b",)):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.md"
            radar.write_report(str(path), list(repos), found)
            return path.read_text(encoding="utf-8")

    def test_clean_sweep(self):
        text = self.run_written([])
        self.assertIn("nothing failed", text)
        self.assertNotIn("|", text)

    def test_lists_failures(self):
        run = {
            "display_title": "Publish the site",
            "html_url": "https://github.com/a/b/actions/runs/1",
            "conclusion": "failure",
        }
        text = self.run_written([("a/b", run)])
        self.assertIn("1 failed run(s)", text)
        self.assertIn("[Publish the site](https://github.com/a/b/actions/runs/1)", text)

    def test_escapes_pipes_in_a_commit_title(self):
        run = {"display_title": "fix a|b parsing", "html_url": "u", "conclusion": "failure"}
        self.assertIn(r"fix a\|b parsing", self.run_written([("a/b", run)]))


class Quote(unittest.TestCase):
    def test_escapes_for_applescript(self):
        self.assertEqual(radar.quote('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(radar.quote("back\\slash"), '"back\\\\slash"')


if __name__ == "__main__":
    unittest.main()
