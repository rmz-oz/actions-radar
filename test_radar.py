#!/usr/bin/env python3
"""Tests for the parts that read someone else's output.

Everything here is offline. The pieces worth testing are the ones handed
whatever GitHub or git decides to send, not the HTTP plumbing.
"""

import http.client
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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


def log(*lines):
    return "\n".join("2026-10-10T19:54:28.000Z " + ln for ln in lines)


class FindCauses(unittest.TestCase):
    """Trimmed from real failing runs; the reason sits above the exit code line."""

    def causes(self, *lines):
        return radar.find_causes(radar.clean_lines(log(*lines)))

    def test_gradle_names_the_test(self):
        got = self.causes(
            "##[group]Run ./gradlew test",
            "##[endgroup]",
            "CogulTest > arabicNounForms FAILED",
            "    org.junit.ComparisonFailure at CogulTest.kt:40",
            "524 tests completed, 1 failed, 219 skipped",
            "* What went wrong:",
            "Execution failed for task ':app:testGenelDebugUnitTest'.",
            "BUILD FAILED in 7m 12s",
            "Configuration cache entry stored.",
            "##[error]Process completed with exit code 1.",
        )
        self.assertEqual(got[0], "CogulTest > arabicNounForms FAILED")
        self.assertIn("* What went wrong: Execution failed for task ':app:testGenelDebugUnitTest'.", got)

    def test_gradle_cause_below_the_task(self):
        got = self.causes(
            "* What went wrong:",
            "Execution failed for task ':app:packageArastirmaRelease'.",
            "> A failure occurred while executing PackageAndroidArtifact$IncrementalSplitterRunnable",
            "   > com.android.ide.common.signing.KeytoolException: Failed to read key snorlax-upload: Tag number over 30 is not supported",
            "##[error]Process completed with exit code 1.",
        )
        self.assertTrue(got[0].startswith("> com.android.ide.common.signing.KeytoolException"))

    def test_pytest_assertion_beats_the_import_error(self):
        got = self.causes(
            "##[group]Run tox",
            "##[endgroup]",
            "ImportError while loading conftest '/w/tests/conftest.py'.",
            "E   DeprecationWarning: The 'ImmutableDict' class is deprecated and will be removed.",
            "##[error]Process completed with exit code 4.",
        )
        self.assertTrue(got[0].startswith("E   DeprecationWarning"))

    def test_rust_panic(self):
        got = self.causes(
            "thread 'feature::f478' (9338) panicked at tests/feature.rs:1195:9:",
            "test result: FAILED. 333 passed; 1 failed",
            "\x1b[1m\x1b[91merror\x1b[0m: test failed, to rerun pass `-p ripgrep --test integration`",
            "##[error]Process completed with exit code 101.",
        )
        self.assertEqual(got, ["thread 'feature::f478' (9338) panicked at tests/feature.rs:1195:9:"])

    def test_strips_colour_codes(self):
        got = self.causes("\x1b[31mError\x1b[39m: ENOENT: no such file", "##[error]Process completed with exit code 1.")
        self.assertEqual(got, ["Error: ENOENT: no such file"])

    def test_jest_details_come_after_the_annotation(self):
        got = self.causes(
            "##[error]expect(received).toMatch(expected)",
            "  \u25cf check is not leaking memory",
            "    expect(received).toMatch(expected)",
        )
        self.assertEqual(got[0], "\u25cf check is not leaking memory")

    def test_annotation_message_counts(self):
        got = self.causes("go: downloading x v1", "##[error]codec.go:32:2: replacement directory does not exist")
        self.assertEqual(got, ["codec.go:32:2: replacement directory does not exist"])

    def test_gradle_summary_beats_noise_from_passing_tests(self):
        got = self.causes(
            "jakarta.jms.JMSException: Cannot connect to server(s).",
            "* What went wrong:",
            "Execution failed for task ':platform:bomrCheck'.",
            "##[error]Process completed with exit code 1.",
        )
        self.assertEqual(got[0], "* What went wrong: Execution failed for task ':platform:bomrCheck'.")

    def test_only_looks_inside_the_failed_step(self):
        got = self.causes(
            "##[group]Run npm ci",
            "npm error old warning from an earlier step",
            "##[group]Run npm test",
            "##[endgroup]",
            "Error: boom",
            "##[error]Process completed with exit code 1.",
        )
        self.assertEqual(got, ["Error: boom"])

    def test_nothing_recognisable(self):
        self.assertEqual(self.causes("all good", "##[error]Process completed with exit code 1."), [])


class LatestAttempts(unittest.TestCase):
    def test_keeps_the_newest_run_per_workflow(self):
        runs = [
            {"id": 1, "workflow_id": 7, "created_at": "2026-10-10T10:00:00Z"},
            {"id": 2, "workflow_id": 7, "created_at": "2026-10-10T11:00:00Z"},
            {"id": 3, "workflow_id": 8, "created_at": "2026-10-10T10:30:00Z"},
        ]
        self.assertEqual([r["id"] for r in radar.latest_attempts(runs)], [3, 2])


class Network(unittest.TestCase):
    def test_dropped_connection_is_an_error_not_a_crash(self):
        with mock.patch("urllib.request.urlopen", side_effect=http.client.RemoteDisconnected("closed")):
            with self.assertRaises(radar.RadarError):
                radar.request("t", "/repos/a/b")


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
        jobs = [{"why": ["CogulTest > arabicNounForms FAILED"]}]
        text = self.run_written([("a/b", run, jobs)])
        self.assertIn("1 failed run(s)", text)
        self.assertIn("| CogulTest > arabicNounForms FAILED |", text)
        self.assertIn("[Publish the site](https://github.com/a/b/actions/runs/1)", text)

    def test_escapes_pipes_in_a_commit_title(self):
        run = {"display_title": "fix a|b parsing", "html_url": "u", "conclusion": "failure"}
        self.assertIn(r"fix a\|b parsing", self.run_written([("a/b", run, [])]))


class Quote(unittest.TestCase):
    def test_escapes_for_applescript(self):
        self.assertEqual(radar.quote('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(radar.quote("back\\slash"), '"back\\\\slash"')


if __name__ == "__main__":
    unittest.main()
