"""Step 2.1 — runs a branch's code in a subprocess and reports test results.

Fail-open discipline (CLAUDE.md): a subprocess crash, syntax error, or
timeout must never raise out of run_tests — it always returns a TestResult,
with errors=1 and pass_rate=0.0 for anything that went wrong. The evaluator
depends on this never blowing up the whole branch-scoring pass.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass
class TestResult:
    passed: int
    failed: int
    errors: int
    pass_rate: float  # passed / (passed + failed + errors)
    output: str  # raw stdout/stderr from the test run
    coverage: Optional[float] = None


def _as_text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _detect_framework(code: str) -> str:
    if re.search(r"^\s*(import|from)\s+pytest\b", code, re.MULTILINE):
        return "pytest"
    if re.search(r"^\s*import\s+unittest\b", code, re.MULTILINE):
        return "unittest"
    return "none"


def run_tests(code: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> TestResult:
    """Execute `code` in a temp file via subprocess, run any embedded tests."""
    framework = _detect_framework(code)
    if framework == "none":
        return TestResult(passed=0, failed=0, errors=0, pass_rate=0.0, output="")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.py"
        try:
            path.write_text(code)
        except OSError as e:
            return TestResult(passed=0, failed=0, errors=1, pass_rate=0.0, output=str(e))

        cmd = (
            [sys.executable, "-m", "pytest", str(path), "-v", "--no-header", "-p", "no:cacheprovider"]
            if framework == "pytest"
            else [sys.executable, "-m", "unittest", "candidate", "-v"]
        )

        try:
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            # TimeoutExpired's captured output can come back as bytes even
            # with text=True (a real crash found by the test for this path).
            output = _as_text(e.stdout) + _as_text(e.stderr)
            return TestResult(
                passed=0, failed=0, errors=1, pass_rate=0.0,
                output=f"Timed out after {timeout}s\n{output}",
            )
        except Exception as e:  # subprocess itself failed to launch, etc.
            return TestResult(passed=0, failed=0, errors=1, pass_rate=0.0, output=str(e))

        output = proc.stdout + proc.stderr
        passed, failed, errors = _parse_counts(output, framework)
        total = passed + failed + errors
        pass_rate = passed / total if total else 0.0
        return TestResult(passed=passed, failed=failed, errors=errors, pass_rate=pass_rate, output=output)


def _parse_counts(output: str, framework: str) -> tuple[int, int, int]:
    if framework == "pytest":
        passed = len(re.findall(r"PASSED", output))
        failed = len(re.findall(r"FAILED", output))
        errors = len(re.findall(r"\bERROR\b", output))
    else:
        passed = len(re.findall(r"\.\.\. ok$", output, re.MULTILINE))
        failed = len(re.findall(r"\.\.\. FAIL$", output, re.MULTILINE))
        errors = len(re.findall(r"\.\.\. ERROR$", output, re.MULTILINE))

    if passed == failed == errors == 0:
        # Nothing parsed at all — a collection failure, an import error, or
        # some other crash we didn't recognize. Never silently report 0/0/0
        # pass_rate=0.0 as if tests genuinely ran and found nothing.
        return 0, 0, 1
    return passed, failed, errors


def has_test_imports(code: str) -> bool:
    """Used by the evaluator (test-gate) and by retroactive rescoring."""
    return _detect_framework(code) != "none"
