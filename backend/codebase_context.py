"""Step 3.6 — gathers a text snapshot of a workspace for `codebase_context`.

Deliberately simple, not a full git-aware indexer: this is a POC-scoped
best-effort snapshot, not a semantic code index. Known limitations, on
record rather than silent: only the workspace root's own `.gitignore` is
read (no nested `.gitignore` files, no `!negation` patterns) — good enough
for a typical small-to-medium project, not accurate for a complex
monorepo. Large projects will hit `MAX_TOTAL_CHARS` and truncate; there is
no retrieval/relevance ranking here, just inclusion in walk order.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List

# Directories never worth including regardless of .gitignore.
EXCLUDED_DIR_NAMES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "out", ".next", "target", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".idea", ".vscode", "coverage",
    ".tox", "vendor",
}

# Skip by extension as a fast path (avoids a decode attempt on obvious
# binaries) — not exhaustive, just the common cases.
EXCLUDED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".pdf", ".zip",
    ".tar", ".gz", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
    ".mov", ".exe", ".dll", ".so", ".dylib", ".pyc", ".class", ".jar",
    ".db", ".sqlite", ".lock",
}

MAX_FILE_BYTES = 100_000  # skip individual files bigger than this
MAX_TOTAL_CHARS = 300_000  # hard cap on the whole snapshot


def _parse_gitignore(root: Path) -> List[str]:
    """Simple glob patterns only — no negation, no directory-only `/` rules
    beyond a trailing slash being treated the same as the bare name."""
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    patterns = []
    try:
        for line in gi.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            patterns.append(line.rstrip("/"))
    except OSError:
        return []
    return patterns


def _is_gitignored(rel_path: Path, patterns: List[str]) -> bool:
    parts = rel_path.parts
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path.as_posix(), pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def gather_codebase_context(root: Path) -> str:
    """Walk `root`, return a text snapshot of its (reasonable) source files.

    Never raises on an individual unreadable/binary file — just skips it.
    """
    patterns = _parse_gitignore(root)
    chunks: List[str] = []
    total = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDED_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if path.suffix.lower() in EXCLUDED_EXTENSIONS:
            continue
        if _is_gitignored(rel, patterns):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable or binary — skip, don't fail the whole gather

        chunk = f"--- {rel.as_posix()} ---\n{content}\n\n"
        if total + len(chunk) > MAX_TOTAL_CHARS:
            chunks.append(f"--- (truncated: workspace exceeds {MAX_TOTAL_CHARS} char snapshot limit) ---\n")
            break
        chunks.append(chunk)
        total += len(chunk)

    return "".join(chunks)
