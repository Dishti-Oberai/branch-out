"""Phase 1/3 follow-up (2026-09-27): relevance-scoped codebase context.

The caching fix (see ROADMAP.md) stopped `codebase_context` from being
re-billed at full price on every call, but it still sends the *whole*
snapshot on a cold cache, and root's stored snapshot is always the whole
codebase regardless. This module is the other half: when a request has a
`target_file`, don't send the whole codebase at all — send just that file
plus its graph neighbors (files it imports, files that import it).

Deliberately NOT full GraphRAG (no embeddings, no external services, no
indexing/summarization pipeline) — a static import/dependency graph built
straight from the already-gathered snapshot text, in the same
deterministic, dependency-free style as `codebase_context.py` itself
(`.gitignore` via fnmatch, no ML). Python imports are resolved via `ast`
(robust); JS/TS imports via a conservative regex (relative imports only —
bare package names are node_modules/external, never in our own snapshot
anyway). Fails open throughout: an unparseable file just contributes no
edges rather than breaking the whole selection, and a `target_file` that
isn't found in the snapshot (outside the workspace, excluded as binary/
oversized, etc.) makes `select_relevant_subset` return None so the caller
falls back to the full snapshot rather than silently sending nothing.
"""
from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Set

_CHUNK_HEADER_RE = re.compile(r"(?m)^--- (.+?) ---\n")

_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
_JS_IMPORT_RE = re.compile(r"""(?:from\s+|require\(\s*)['"](\.[^'"]+)['"]""")


def parse_snapshot_chunks(snapshot: str) -> Dict[str, str]:
    """Splits a `gather_codebase_context()` snapshot back into
    {relpath: content}. Skips the truncation marker line (same `--- ... ---`
    shape but not a real file) and anything else that doesn't look like a
    real relative path.
    """
    parts = _CHUNK_HEADER_RE.split(snapshot)
    # parts = ['', path1, content1, path2, content2, ...] -- leading '' is
    # whatever came before the first header (nothing, in practice).
    chunks: Dict[str, str] = {}
    for i in range(1, len(parts), 2):
        path = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        if path.startswith("("):
            continue  # the truncation marker, not a file
        # Each chunk's content ends with the "\n\n" separator baked in by
        # gather_codebase_context -- strip exactly that, not arbitrary
        # trailing whitespace, so real trailing blank lines in the file
        # itself aren't eaten.
        if content.endswith("\n\n"):
            content = content[:-2]
        chunks[path] = content
    return chunks


def _python_import_candidates(module: str, level: int, from_relpath: str) -> List[str]:
    """Builds path candidates for one `import`/`from ... import` statement.
    `level` is 0 for an absolute import, >0 for relative (`from . import x`
    is level=1, `from .. import x` is level=2, etc.) -- mirrors `ast`'s own
    `ImportFrom.level` field.
    """
    if not module:
        return []
    segments = module.split(".")
    if level > 0:
        base_dir = PurePosixPath(from_relpath).parent
        for _ in range(level - 1):
            base_dir = base_dir.parent
        base = base_dir.joinpath(*segments)
    else:
        base = PurePosixPath(*segments)
    return [
        str(base) + ".py",
        str(base / "__init__.py"),
        # Flat single-file style (this project's own backend/*.py: `from
        # models import X` where "models" IS the whole module name, not a
        # package prefix) -- try the bare last segment as a top-level file
        # too, in case `base`'s full dotted path doesn't exist as such.
        segments[-1] + ".py",
    ]


def _extract_python_imports(relpath: str, content: str) -> List[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []  # fail open -- one unparseable file contributes no edges
    candidates: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                candidates.extend(_python_import_candidates(alias.name, 0, relpath))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                candidates.extend(_python_import_candidates(module, node.level, relpath))
            # Also try module.name as its own path -- covers `from pkg
            # import submodule` (a real file: pkg/submodule.py) as well as
            # `from . import b` (module is empty, "b" is the submodule
            # file, not a symbol inside some other file). A false
            # candidate here is harmless: _resolve only matches it if that
            # file actually exists in the snapshot.
            for alias in node.names:
                combined = f"{module}.{alias.name}" if module else alias.name
                candidates.extend(_python_import_candidates(combined, node.level, relpath))
    return candidates


def _extract_js_imports(relpath: str, content: str) -> List[str]:
    base_dir = PurePosixPath(relpath).parent
    candidates: List[str] = []
    for raw in _JS_IMPORT_RE.findall(content):
        resolved = (base_dir / raw).as_posix()
        # Normalize "./a/../b" -> "b" style traversal without touching disk.
        resolved = str(PurePosixPath(resolved))
        candidates.append(resolved)
        for ext in _JS_EXTENSIONS:
            candidates.append(resolved + ext)
            candidates.append(resolved + "/index" + ext)
    return candidates


def _extract_imports(relpath: str, content: str) -> List[str]:
    if relpath.endswith(".py"):
        return _extract_python_imports(relpath, content)
    if relpath.endswith(_JS_EXTENSIONS):
        return _extract_js_imports(relpath, content)
    return []


def _resolve(candidate: str, known: Iterable[str]) -> Optional[str]:
    """Matches an import candidate against the snapshot's real relpaths.
    Exact match first; otherwise a path-boundary suffix match (candidate is
    a right-aligned subpath, e.g. "core/cache_hash.py" matching
    "tokenetics/core/cache_hash.py") -- never a bare substring match, which
    would let "b.py" wrongly match "ab.py".
    """
    if candidate in known:
        return candidate
    for path in known:
        if path == candidate or path.endswith("/" + candidate):
            return path
    return None


def build_import_graph(chunks: Dict[str, str]) -> Dict[str, Set[str]]:
    """Undirected adjacency: for each file, the set of files it imports
    AND the files that import it -- either direction indicates relevance
    for context purposes, not just the "depends on" direction.
    """
    graph: Dict[str, Set[str]] = {path: set() for path in chunks}
    known = set(chunks)
    for relpath, content in chunks.items():
        for raw_candidate in _extract_imports(relpath, content):
            target = _resolve(raw_candidate, known)
            if target and target != relpath:
                graph[relpath].add(target)
                graph[target].add(relpath)
    return graph


def _match_target_relpath(target_file: str, known: Iterable[str]) -> Optional[str]:
    """`target_file` may be an absolute fsPath (VS Code) or a short
    workspace-relative path (web app) -- match by path-boundary suffix
    against the snapshot's known relpaths, same rule as `_resolve` above.
    """
    normalized = target_file.replace("\\", "/")
    return _resolve(normalized, known) or next(
        (p for p in known if normalized.endswith("/" + p) or normalized == p), None
    )


def select_relevant_subset(snapshot: str, target_file: str, depth: int = 1) -> Optional[str]:
    """Returns a snapshot containing only `target_file` and its import-graph
    neighbors out to `depth` hops, in the same chunk format as the input --
    or None if `target_file` isn't found in the snapshot at all (outside
    the workspace, excluded as binary/oversized, etc.), so the caller can
    fall back to the full snapshot rather than silently sending nothing.
    """
    chunks = parse_snapshot_chunks(snapshot)
    root = _match_target_relpath(target_file, chunks)
    if root is None:
        return None

    graph = build_import_graph(chunks)
    selected: Set[str] = {root}
    frontier = {root}
    for _ in range(depth):
        next_frontier: Set[str] = set()
        for node in frontier:
            next_frontier |= graph.get(node, set())
        next_frontier -= selected
        selected |= next_frontier
        frontier = next_frontier
        if not frontier:
            break

    return "".join(f"--- {path} ---\n{chunks[path]}\n\n" for path in sorted(selected))


# How many import-graph hops out from a target_file to include. 1 = the
# file plus files it directly imports or is imported by. Not exposed as an
# API parameter -- not a decision worth a whole request-schema field yet; a
# fixed, documented constant is enough for a POC and easy to bump later if
# it proves too narrow in practice.
CODEBASE_GRAPH_DEPTH = 1


def codebase_context_for_prompt(
    codebase_context: Optional[str], target_file: Optional[str], include_codebase: bool
) -> Optional[str]:
    """Decides what codebase context (if any) actually goes in one LLM
    call's prompt. Shared by `orchestrator.py` (branch/continue) and
    `context_retriever.py` (Phase 4 queries) -- originally lived only in
    orchestrator.py, promoted here 2026-09-27 so both call sites share one
    implementation instead of two copies drifting apart.

    Deliberately does NOT touch a node's own *stored* `codebase_context` --
    callers are responsible for keeping that as the full snapshot regardless
    of what any single call chose to send.

    - `include_codebase=False` -> None, always.
    - No `target_file` -> the full snapshot -- nothing to scope a subgraph
      around (e.g. a tree-panel question with no file focus).
    - `target_file` set -> a relevance-scoped subset (target_file plus its
      import-graph neighbors). Fails open to the full snapshot if
      `target_file` isn't found in it (outside the workspace, excluded as
      binary/oversized, etc.) rather than silently sending nothing.
    """
    if not include_codebase:
        return None
    if not codebase_context or not target_file:
        return codebase_context
    scoped = select_relevant_subset(codebase_context, target_file, depth=CODEBASE_GRAPH_DEPTH)
    return scoped if scoped is not None else codebase_context
