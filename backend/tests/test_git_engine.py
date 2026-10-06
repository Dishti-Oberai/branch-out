import os

import pytest

from git_engine import make_patch, merge_code


def test_make_patch_produces_a_real_unified_diff():
    patch = make_patch("line1\nline2\n", "line1\nCHANGED\n")
    assert "-line2" in patch
    assert "+CHANGED" in patch
    assert "a/content" in patch and "b/content" in patch


def test_make_patch_of_identical_content_is_empty():
    patch = make_patch("same\n", "same\n")
    assert patch == ""


def test_merge_code_applies_a_clean_patch():
    base = "line1\nline2\nline3\n"
    patch = make_patch(base, "line1\nCHANGED\nline3\n")
    result = merge_code(base, patch)
    assert result.success is True
    assert result.merged_code == "line1\nCHANGED\nline3\n"
    assert result.diff_preview is None


def test_merge_code_applies_cleanly_even_when_base_has_unrelated_extra_content():
    # The patch's context lines still match where they need to -- a
    # realistic "the file grew elsewhere but this part is untouched" case.
    # The changed region has real surrounding context (not at the literal
    # end of the diffed content), and base's extra content is appended
    # AFTER everything the diff touches -- avoids git apply's stricter
    # end-of-hunk-with-no-trailing-context handling, which is a real git
    # behavior, not something to work around in the implementation.
    original = "def a():\n    pass\n\ndef b():\n    pass\n"
    revised = "def a():\n    return 1\n\ndef b():\n    pass\n"
    patch = make_patch(original, revised)
    base = original + "\ndef c():\n    pass\n"
    result = merge_code(base, patch)
    assert result.success is True
    assert "def a():\n    return 1\n" in result.merged_code
    assert "def c():\n    pass\n" in result.merged_code  # untouched content survived


def test_merge_code_reports_conflict_when_base_diverged_incompatibly():
    original = "def f():\n    return 1\n"
    revised = "def f():\n    return 2\n"
    patch = make_patch(original, revised)
    # base's surrounding context doesn't match what the patch expects at all
    base = "def f():\n    return 999  # someone changed this differently\n"
    result = merge_code(base, patch)
    assert result.success is False
    assert result.merged_code is None
    assert result.diff_preview == patch


def test_merge_code_never_raises_on_a_garbage_patch():
    result = merge_code("some code\n", "this is not a diff at all, just text")
    assert result.success is False
    assert result.diff_preview is not None


def test_merge_code_empty_patch_is_a_no_op():
    result = merge_code("hello\n", "")
    # An empty patch is a no-op diff -- applies trivially, base unchanged.
    assert result.success is True
    assert result.merged_code == "hello\n"


def test_make_patch_handles_original_with_no_trailing_newline():
    # Real bug, caught via a real-API check (2026-09-29): a missing
    # trailing newline on `original` used to corrupt the generated patch
    # -- the last removed line and the first added line got concatenated
    # onto one line with no separator, producing an unparseable diff and
    # a spurious conflict even for content that should merge trivially.
    base = "def double(x):\n    return x + x"  # no trailing newline
    revised = 'def double(x):\n    """doc"""\n    return x + x\n'  # has one
    patch = make_patch(base, revised)
    # A pure insertion -- "return x + x" is unchanged context, not
    # removed+re-added, so there must be no "-" line at all. The bug
    # glued "-    return x + x" and "+    \"\"\"doc\"\"\"" onto one
    # corrupted line instead of this clean shape.
    removed = [ln for ln in patch.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert removed == []
    assert '+    """doc"""' in patch.splitlines()


def test_merge_code_applies_cleanly_despite_a_missing_trailing_newline_on_base():
    base = "def double(x):\n    return x + x"  # no trailing newline
    revised = 'def double(x):\n    """doc"""\n    return x + x\n'
    patch = make_patch(base, revised)
    result = merge_code(base, patch)
    assert result.success is True
    assert result.merged_code == 'def double(x):\n    """doc"""\n    return x + x\n'


def test_merge_code_self_patch_with_no_trailing_newline_is_always_clean():
    # The exact real-world shape that caught this: source == target (a
    # trivial self-patch), base lacking a trailing newline -- this must
    # never conflict, since nothing actually diverged.
    code = "def f():\n    pass"  # no trailing newline
    revised = "def f():\n    return 1\n"
    patch = make_patch(code, revised)
    result = merge_code(code, patch)
    assert result.success is True


def test_merge_code_cleans_up_its_temp_directory_on_success(monkeypatch):
    import git_engine

    created = {}
    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created["path"] = path
        return path

    monkeypatch.setattr(git_engine.tempfile, "mkdtemp", spy_mkdtemp)
    base = "a\n"
    patch = make_patch(base, "b\n")
    merge_code(base, patch)
    assert "path" in created
    assert not os.path.exists(created["path"])


def test_merge_code_cleans_up_its_temp_directory_on_conflict(monkeypatch):
    import git_engine

    created = {}
    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created["path"] = path
        return path

    monkeypatch.setattr(git_engine.tempfile, "mkdtemp", spy_mkdtemp)
    merge_code("totally different\n", "garbage, not a diff")
    assert "path" in created
    assert not os.path.exists(created["path"])
