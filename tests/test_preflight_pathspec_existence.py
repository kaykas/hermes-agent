#!/usr/bin/env python3
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "preflight-pathspec-existence.py"
SPEC = importlib.util.spec_from_file_location("preflight_pathspec_existence", SCRIPT)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PathspecExistenceTests(unittest.TestCase):
    def test_missing_pathspec_fails_before_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--cwd", tmp, "--pathspec", "scripts/missing.py"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("scripts/missing.py", result.stderr)
            self.assertIn("does not exist", result.stderr)
            self.assertIn("command not spawned", result.stderr)

    def test_existing_pathspec_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp, "scripts", "present.py")
            target.parent.mkdir()
            target.write_text("print('ok')\n")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--cwd", tmp, "--pathspec", "scripts/present.py"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_list_passes(self):
        self.assertEqual(MODULE.check_pathspecs([], os.getcwd()), [])

    def test_ref_only_arguments_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(MODULE.check_pathspecs(["main", "release", "613e497fb1"], tmp), [])

    def test_relative_paths_resolve_against_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = MODULE.check_pathspecs(["scripts/missing.py"], tmp)
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0].resolved, os.path.join(tmp, "scripts", "missing.py"))

    def test_glob_requires_at_least_one_existing_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "one.py").write_text("")
            self.assertEqual(MODULE.check_pathspecs(["*.py"], tmp), [])
            self.assertEqual(len(MODULE.check_pathspecs(["*.js"], tmp)), 1)


if __name__ == "__main__":
    unittest.main()
