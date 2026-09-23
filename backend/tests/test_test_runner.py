from test_runner import has_test_imports, run_tests

PASSING_UNITTEST = """
import unittest

def add(a, b):
    return a + b

class T(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

if __name__ == "__main__":
    unittest.main()
"""

FAILING_UNITTEST = """
import unittest

def add(a, b):
    return a - b  # bug

class T(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

if __name__ == "__main__":
    unittest.main()
"""

PASSING_PYTEST = """
import pytest

def mul(a, b):
    return a * b

def test_mul():
    assert mul(2, 3) == 6
"""

NO_TESTS = """
def add(a, b):
    return a + b
"""

SYNTAX_ERROR = """
import unittest

def broken(:
    pass
"""

TIMES_OUT = """
import unittest
import time

class T(unittest.TestCase):
    def test_slow(self):
        time.sleep(30)

if __name__ == "__main__":
    unittest.main()
"""


def test_passing_unittest_has_full_pass_rate():
    result = run_tests(PASSING_UNITTEST)
    assert result.pass_rate == 1.0
    assert result.errors == 0


def test_failing_unittest_has_partial_pass_rate():
    result = run_tests(FAILING_UNITTEST)
    assert result.pass_rate < 1.0


def test_passing_pytest_has_full_pass_rate():
    result = run_tests(PASSING_PYTEST)
    assert result.pass_rate == 1.0


def test_syntax_error_reports_error_without_raising():
    result = run_tests(SYNTAX_ERROR)
    assert result.errors == 1
    assert result.pass_rate == 0.0


def test_code_with_no_tests_returns_zeros_without_raising():
    result = run_tests(NO_TESTS)
    assert (result.passed, result.failed, result.errors) == (0, 0, 0)
    assert result.pass_rate == 0.0


def test_timeout_returns_after_deadline_without_hanging():
    result = run_tests(TIMES_OUT, timeout=0.5)
    assert result.errors == 1
    assert "Timed out" in result.output


def test_has_test_imports():
    assert has_test_imports(PASSING_UNITTEST) is True
    assert has_test_imports(PASSING_PYTEST) is True
    assert has_test_imports(NO_TESTS) is False
