import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.terminal_heredoc_scratch_guard import reject_python_heredoc, require_scratch_py_compile
from hermes_constants import get_hermes_home

def scratch_dir() -> Path:
    path = get_hermes_home() / "cache" / "scratch"
    path.mkdir(parents=True, exist_ok=True)
    return path


class TerminalHeredocScratchGuardTests(unittest.TestCase):
    def test_five_line_python_heredoc_is_rejected(self):
        command = "python3 - <<'EOF'\na=1\nb=2\nc=3\nd=4\nprint(a+b+c+d)\nEOF"
        reason = reject_python_heredoc(command)
        if reason is None:
            self.fail("long heredoc was not rejected")
        self.assertIn("write_file", reason)
        self.assertIn("cache/scratch", reason)

    def test_unterminated_python_heredoc_is_rejected(self):
        self.assertIsNotNone(reject_python_heredoc("python3 - <<'EOF'\na=1\nb=2\nc=3\nd=4"))

    def test_two_line_python_heredoc_passes(self):
        self.assertIsNone(reject_python_heredoc("python3 - <<'EOF'\nprint('ok')\nEOF"))

    def test_non_python_heredoc_passes(self):
        self.assertIsNone(reject_python_heredoc("bash <<'EOF'\na=1\nb=2\nc=3\nd=4\nEOF"))

    def test_valid_scratch_script_passes_py_compile(self):
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as tmp:
            script = Path(tmp) / "valid.py"
            script.write_text("print('ok')\n")
            self.assertIsNone(require_scratch_py_compile(f"python3 {script}"))

    def test_invalid_scratch_script_fails_before_execution(self):
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as tmp:
            script = Path(tmp) / "invalid.py"
            script.write_text("def broken(:\n")
            reason = require_scratch_py_compile(f"python3 {script}")
            if reason is None:
                self.fail("invalid scratch script was not rejected")
            self.assertIn("failed py_compile", reason)

    def test_non_scratch_python_path_passes_unguarded(self):
        self.assertIsNone(require_scratch_py_compile("python3 /tmp/not-scratch.py"))

    def test_empty_command_passes(self):
        self.assertIsNone(reject_python_heredoc(""))
        self.assertIsNone(require_scratch_py_compile(""))

    def test_terminal_pre_exec_rejects_long_heredoc(self):
        from tools.terminal_tool import _Rejected, _pre_exec_block
        command = "python3 - <<'EOF'\na=1\nb=2\nc=3\nd=4\nprint(a)\nEOF"
        with self.assertRaises(_Rejected):
            _pre_exec_block(command, env=None, env_type="local", cwd=str(ROOT), workdir=None, session_key="test")


if __name__ == "__main__":
    unittest.main()
