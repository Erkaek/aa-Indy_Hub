"""Offline tests of evidence gating and repeatable isolated environments."""

# Standard Library
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "watch", Path(__file__).with_name("runner.py")
)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

restore_spec = importlib.util.spec_from_file_location(
    "restore", Path(__file__).with_name("restore.py")
)
restore_module = importlib.util.module_from_spec(restore_spec)
restore_spec.loader.exec_module(restore_module)


class WatchTests(unittest.TestCase):
    def test_only_repeatable_failure_with_passing_control_is_confirmed(self):
        candidate = {"status": "failed", "phase": "tests", "failure_key": "same"}
        passed = {"status": "passed"}
        self.assertEqual(watch.verdict(passed, None, None), "healthy")
        self.assertEqual(watch.verdict(candidate, passed, candidate), "confirmed")
        for control, repeat in (
            (None, candidate),
            (candidate, candidate),
            (passed, None),
            (passed, {**candidate, "phase": "check"}),
            (passed, {**candidate, "failure_key": "different"}),
        ):
            with self.subTest(control=control, repeat=repeat):
                self.assertEqual(
                    watch.verdict(candidate, control, repeat), "unverified"
                )
        self.assertEqual(
            watch.verdict({**candidate, "failure_key": None}, passed, candidate),
            "unverified",
        )

    def test_infrastructure_and_unclassified_failures_are_not_code_regressions(self):
        for phase, output in (
            ("install", "Could not fetch URL: ResolutionImpossible"),
            ("tests", "ERROR: test_api\nNetwork is unreachable"),
            ("tests", "Process exceeded the limit"),
            ("install", "No matching distribution found"),
        ):
            self.assertIsNone(watch.failure_key(phase, output))
        self.assertEqual(
            watch.failure_key("tests", "ERROR: test_api (suite.Test)\nElapsed 4"),
            watch.failure_key("tests", "ERROR: test_api (suite.Test)\nElapsed 9"),
        )
        self.assertEqual(
            watch.failure_key("install", "ResolutionImpossible"), "resolver-conflict"
        )

    def test_expected_network_errors_from_passing_tests_do_not_hide_regressions(self):
        output = "test_expected_failure ... ConnectionError\nOK\nERROR: test_real_failure (suite.Test)\nAssertionError: version mismatch"
        self.assertIsNotNone(watch.failure_key("tests", output))

    def test_controller_confirms_and_retains_the_passing_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            spec = watch.contract(Path(__file__).resolve().parents[2], "charlink")
            saved = {
                "schema": watch.SCHEMA,
                "contract": watch.fingerprint(spec),
                "versions": {"django": "5.2"},
            }
            baseline.write_text(json.dumps(saved))
            failure = {
                "status": "failed",
                "phase": "tests",
                "failure_key": "stable",
                "versions": {"django": "5.3"},
            }
            control = {"status": "passed", "versions": {"django": "5.2"}}
            argv = [
                "runner",
                "--profile",
                "charlink",
                "--output",
                str(root / "out"),
                "--baseline",
                str(baseline),
            ]
            with (
                patch.object(watch.sys, "argv", argv),
                patch.object(
                    watch, "attempt", side_effect=[failure, control, failure.copy()]
                ) as attempt,
            ):
                watch.main()
            self.assertEqual(attempt.call_count, 3)
            self.assertEqual(
                json.loads((root / "out/result.json").read_text())["status"],
                "confirmed",
            )
            self.assertEqual(json.loads(baseline.read_text()), saved)

    def test_baseline_restore_reads_json_without_extracting_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, target = root / "baseline.zip", root / "output/python.json"
            restore_module.restore(archive, target)
            self.assertFalse(target.exists())
            for name, accepted in (("python.json", True), ("../python.json", False)):
                with zipfile.ZipFile(archive, "w") as source:
                    source.writestr(name, '{"versions":{"django":"5.2"}}')
                if accepted:
                    restore_module.restore(archive, target)
                    self.assertEqual(
                        json.loads(target.read_text())["versions"], {"django": "5.2"}
                    )
                else:
                    with self.assertRaises(ValueError):
                        restore_module.restore(archive, target)

    def test_pins_reject_requirement_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pins.txt"
            watch.write_constraints(path, {"django": "5.2.19"})
            self.assertEqual(path.read_text(), "django==5.2.19\n")
            for pins in ({}, {"--index-url": "evil"}, {"django": "5\n-r secret"}):
                with self.assertRaises(ValueError):
                    watch.write_constraints(path, pins)

    def test_profile_uses_actual_manifest_and_tox_dependencies(self):
        root = Path(__file__).resolve().parents[2]
        base = watch.contract(root, "base")
        charlink = watch.contract(root, "charlink")
        self.assertFalse(any(req.startswith("aa-charlink") for req in base["tests"]))
        self.assertTrue(any(req.startswith("aa-charlink") for req in charlink["tests"]))
        self.assertEqual(
            watch.contract(root, "aadiscordbot")["extra"], ["allianceauth-discordbot"]
        )
        self.assertFalse(any("mysql:" in req for req in base["tests"]))

    def test_attempts_copy_source_and_never_reuse_candidate_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pristine"
            source.mkdir()
            (source / "marker").write_text("original")

            def fake_run(command, log, *, cwd, env):
                self.assertEqual((cwd / "marker").read_text(), "original")
                (cwd / "marker").write_text("mutated")
                return False, "ERROR: test_changed (suite.Test)"

            with (
                patch.object(watch.venv.EnvBuilder, "create"),
                patch.object(watch, "run", side_effect=fake_run),
            ):
                for name in ("candidate", "control"):
                    result = watch.attempt(
                        source, root / name, {"profile": "base", "tests": []}
                    )
                    self.assertEqual(result["status"], "failed")
            self.assertEqual((source / "marker").read_text(), "original")


if __name__ == "__main__":
    unittest.main()
