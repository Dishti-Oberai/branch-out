"""Phase 5.1: real git-based merge/conflict detection.

`merge_code` applies a unified diff (`patch`) onto some target content
(`base`) using a real, temporary git repository's `git apply` — genuine
context-aware conflict detection (does the patch's surrounding context
actually match what's in `base`?), not a naive string-replace or a blind
overwrite. This is the real fix Step 3.9's interim hash-check safety net
was always meant to be superseded by (see ROADMAP.md's Phase 3 incident
log and Phase 5's note).

`patch` is expected to be a unified diff computed against whatever content
actually diverged from `base` -- typically (a past node's original code ->
that same code with a clarification incorporated), applied onto `base`
(the current/active node's code, which may have moved on independently
down a different branch). If the diff's context doesn't match what's
actually in `base`, `git apply` fails and this reports a conflict rather
than guessing at a resolution (CLAUDE.md's git-merge fail-open rule:
"surface the conflict, never silently apply a broken merge").

`make_patch` is the companion half: builds a unified diff in exactly the
format `merge_code` expects, so callers (`/patch`, `/merge`) don't need to
know `git apply`'s header conventions themselves.
"""
from __future__ import annotations

import difflib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import git

# The filename inside the throwaway repo. Never touches a real path on
# disk -- this whole repo exists only in a temp dir for the duration of one
# merge_code() call and is always removed afterward, success or failure.
_FILENAME = "content"


@dataclass
class MergeResult:
    success: bool
    merged_code: Optional[str] = None
    diff_preview: Optional[str] = None


def _ensure_trailing_newline(text: str) -> str:
    """Real bug, caught via a real-API check (2026-09-29): `difflib.
    unified_diff` requires every input line to end with "\\n" to serialize
    correctly. When `text` doesn't (a genuinely common case -- LLM-
    generated code very often differs in trailing-newline convention from
    whatever's already stored), the last "-" line and the first following
    "+" line get concatenated onto ONE line in the diff output with no
    separator between them (no newline to put one there), producing a
    corrupted patch that `git apply` correctly rejects as unparseable --
    surfacing as a spurious conflict on what should have been a clean,
    trivial change. Reproduced directly: `make_patch("...x + x", "...x + x\\n")`
    (no trailing newline on one side) produced a mangled hunk and a false
    conflict even with `base` and `original` byte-identical.

    Fix: normalize every string that flows through `make_patch`/`merge_code`
    to always end with "\\n" before diffing or writing to disk. This can
    make a round-tripped file gain a trailing newline it didn't have
    before -- an accepted, minor, and honestly overdue normalization
    (near-universal convention for text files), not a content change
    worth surfacing as its own conflict.
    """
    if text and not text.endswith("\n"):
        return text + "\n"
    return text


def make_patch(original: str, revised: str) -> str:
    """Builds a unified diff from `original` to `revised`, in the `a/`/`b/`
    header format `merge_code`'s `git apply -p1` (git's own default) call
    expects.
    """
    diff = difflib.unified_diff(
        _ensure_trailing_newline(original).splitlines(keepends=True),
        _ensure_trailing_newline(revised).splitlines(keepends=True),
        fromfile=f"a/{_FILENAME}",
        tofile=f"b/{_FILENAME}",
    )
    return "".join(diff)


def merge_code(base: str, patch: str) -> MergeResult:
    """Applies `patch` onto `base`. Always runs in a fresh temp git repo,
    always cleaned up -- even on an unexpected error, never leaves a
    lingering directory behind.

    A patch that fails to apply cleanly (context doesn't match `base`) or
    isn't a valid diff at all both come back as `success=False` with the
    raw patch text as `diff_preview` -- this never raises; an invalid
    patch is just another kind of conflict, not a crash.
    """
    if not patch.strip():
        # A no-op diff (e.g. make_patch() of identical content) trivially
        # "applies" -- nothing to reconcile. `git apply` itself treats an
        # empty patch file as an error ("unrecognized input"), which would
        # otherwise misreport a genuinely clean case as a conflict.
        return MergeResult(success=True, merged_code=base)

    tmpdir = tempfile.mkdtemp(prefix="branchout-merge-")
    try:
        repo = git.Repo.init(tmpdir)
        # Explicit local identity -- never depend on the environment
        # having a global git user.name/user.email configured (CI, a
        # fresh sandbox, etc. often don't).
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Branch-Out")
            cw.set_value("user", "email", "branch-out@localhost")

        target = Path(tmpdir) / _FILENAME
        target.write_text(_ensure_trailing_newline(base))
        repo.index.add([str(target)])
        repo.index.commit("base")

        patch_file = Path(tmpdir) / "patch.diff"
        patch_file.write_text(patch)

        try:
            repo.git.apply(str(patch_file))
        except git.GitCommandError:
            return MergeResult(success=False, diff_preview=patch)

        return MergeResult(success=True, merged_code=target.read_text())
    except Exception:
        # Fail-open: anything else unexpected (malformed patch text git's
        # own parser chokes on before even trying to apply it, an
        # encoding issue, etc.) is a conflict we can't resolve, never a
        # crash for the caller.
        return MergeResult(success=False, diff_preview=patch)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
