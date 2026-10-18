"""Step 2.5 — benchmark runner: linear baselines vs Branch-Out.

Two baselines in one run (2026-09-23 decision, see ROADMAP.md):
  --baseline-generic : the raw task prompt, no engineering. The headline,
                        intuitively-understood comparison.
  --baseline-matched  : same context/detail Branch-Out itself receives,
                         single LLM call, no branching. Pre-empts "isn't
                         this just prompting?" — same prompt engineering,
                         the only difference is whether you branch.
  --branchout         : full flow — beam_width=3, auto-picks whichever of
                         the 3 branches scores highest against the task's
                         own test_suite (this is the automated stand-in for
                         a human picking a branch; see the honesty
                         requirements below).
  --dry-run           : load and validate every task file, print counts,
                         make no LLM calls at all. No API key needed.

Honesty requirements (ROADMAP.md, carried from Tokenetics' benchmarking
discipline): report per-category (standard vs custom), never a single flat
percentage; never tune the task set after seeing results; document losses,
not just wins.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Real bug, found running this for real for the first time (2026-10-18):
# `main.py` (the FastAPI app) calls load_dotenv() at import time, which is
# why every prior "real-API verification" in ROADMAP.md worked -- it went
# through the running app. This script never imports main.py, so
# ANTHROPIC_API_KEY was never actually in the environment here; every
# single call failed authentication and the fail-open per-task try/except
# below quietly recorded it as a legitimate 0.0 score for every task in
# every mode -- a fully-populated, entirely fake "0% across the board"
# result that would have been reported as real data if not caught.
# Loads explicitly from BACKEND_DIR's .env (not cwd-relative) so this
# works regardless of which directory the script is invoked from.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

TASK_DIRS = ("standard", "custom")
REQUIRED_FIELDS = ("id", "description", "prompt", "test_suite", "category")


@dataclass
class Task:
    id: str
    description: str
    prompt: str
    test_suite: str
    category: str


@dataclass
class TaskResult:
    task_id: str
    category: str
    mode: str
    pass_rate: float
    passed: bool  # pass@1 for baselines; "at least one branch passed" for branchout
    # 2026-10-18: distinct from a genuine 0.0 (the model tried and failed
    # the task). Set to the exception's own text when the call itself
    # crashed (e.g. auth, network, rate limit) -- without this, an
    # infrastructure failure and a real model failure were indistinguishable
    # in the summary, which directly undermines "document losses, not just
    # wins": a loss has to be the MODEL's loss, not an unreported outage
    # silently reported as the model's fault.
    error: str | None = None


def load_tasks() -> List[Task]:
    base = Path(__file__).parent
    tasks: List[Task] = []
    for sub in TASK_DIRS:
        for path in sorted((base / sub).glob("*.json")):
            data = json.loads(path.read_text())
            missing = [f for f in REQUIRED_FIELDS if f not in data]
            if missing:
                raise ValueError(f"{path}: missing required field(s) {missing}")
            tasks.append(Task(**{f: data[f] for f in REQUIRED_FIELDS}))
    ids = [t.id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate task ids: {dupes}")
    return tasks


_FENCE_RE = re.compile(r"```(?:python|json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_code(response: str) -> str:
    """Best-effort: branch-generator-style {"code": ...} JSON, a fenced code
    block, or raw text — whichever the prompt actually got back.

    Real bug, caught by a suspiciously-uniform 0.0 across EVERY task under
    --baseline-generic (2026-10-18): that mode's prompt has no system
    instruction steering the model toward a code-only reply (unlike
    --baseline-matched/branchout, which explicitly ask for JSON), so
    Claude's actual, completely normal response is markdown prose with a
    fenced block somewhere in the MIDDLE ("## FizzBuzz in Python\n\n```python
    ...\n```\n\n### How it works:\n| ... |"), confirmed with a real call.
    The old check only stripped a fence when the response STARTED with
    "```" -- any response that didn't (i.e. every one of them here) passed
    the ENTIRE markdown blob, headers/tables/fence-markers included,
    straight to the test runner as "code", a guaranteed syntax error on
    every single task regardless of whether the model's actual solution
    was correct. Finding the first fenced block ANYWHERE in the text
    fixes this for any prompt shape, not just ones with no prose at all.
    """
    text = response.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("{"):
        try:
            return json.loads(text)["code"]
        except Exception:  # noqa: BLE001 — not JSON after all, fall through to raw text
            pass
    return text


def _score(code: str, test_suite: str):
    from test_runner import run_tests
    return run_tests(f"{code}\n\n{test_suite}")


async def run_baseline_generic(task: Task, llm_client) -> TaskResult:
    response = await llm_client.complete(messages=[{"role": "user", "content": task.prompt}], system="")
    result = _score(_extract_code(response), task.test_suite)
    return TaskResult(task.id, task.category, "baseline-generic", result.pass_rate, result.pass_rate == 1.0)


async def run_baseline_matched(task: Task, llm_client) -> TaskResult:
    system = (
        "You are a coding assistant. Solve the task with a single, complete, correct "
        'solution. Return only JSON: {"code": "...", "reasoning": "..."}'
    )
    response = await llm_client.complete(
        messages=[{"role": "user", "content": f"Task: {task.prompt}"}], system=system
    )
    result = _score(_extract_code(response), task.test_suite)
    return TaskResult(task.id, task.category, "baseline-matched", result.pass_rate, result.pass_rate == 1.0)


async def run_branchout(task: Task, llm_client) -> TaskResult:
    from orchestrator import Orchestrator

    o = Orchestrator(llm_client)
    branches = await o.branch("root", task.prompt, beam_width=3)
    if not branches:
        # branch_generator fails open per-branch (CLAUDE.md) -- an empty
        # result here means EVERY one of the 3 parallel calls crashed, not
        # that the model produced 3 genuinely bad answers. Mark it as an
        # error, not a real 0.0, for the same honesty reason as _main_async's
        # per-task except block below.
        return TaskResult(task.id, task.category, "branchout", 0.0, False, error="all branches failed to generate")
    # Automated stand-in for "the user picks a branch": score every branch
    # against the task's own tests and take the best — never averaged, that
    # would understate what branching is actually for.
    rates = [_score(b.code, task.test_suite).pass_rate for b in branches]
    best = max(rates)
    return TaskResult(task.id, task.category, "branchout", best, best == 1.0)


def summarize(results: List[TaskResult]) -> Dict[str, Any]:
    """Per mode, per category — never a single flat number (honesty requirement).

    2026-10-18: pass@1/mean_score are computed only over tasks that actually
    ran (`error is None`) -- an infrastructure failure (auth, network, rate
    limit) must never silently count as the model trying and failing.
    `n`/`errored` together always account for every task attempted, so a
    reader can immediately see whether "0.0" means "the model failed" or
    "this never actually ran."
    """
    by_mode_cat: Dict[str, Dict[str, List[TaskResult]]] = {}
    for r in results:
        by_mode_cat.setdefault(r.mode, {}).setdefault(r.category, []).append(r)
    summary: Dict[str, Any] = {}
    for mode, cats in by_mode_cat.items():
        summary[mode] = {}
        for cat, rs in cats.items():
            ok = [r for r in rs if r.error is None]
            n = len(ok)
            passed = sum(1 for r in ok if r.passed)
            summary[mode][cat] = {
                "n": n,
                "errored": len(rs) - n,
                "pass@1": round(passed / n, 3) if n else None,
                "mean_score": round(sum(r.pass_rate for r in ok) / n, 3) if n else None,
            }
    return summary


async def _main_async(args: argparse.Namespace) -> None:
    tasks = load_tasks()
    n_standard = sum(1 for t in tasks if t.category == "standard")
    n_custom = sum(1 for t in tasks if t.category == "custom")

    if args.dry_run:
        print(f"Loaded {len(tasks)} tasks: {n_standard} standard, {n_custom} custom")
        for t in tasks:
            print(f"  [{t.category:8s}] {t.id:16s} {t.description}")
        return

    from claude_client import ClaudeClient

    llm = ClaudeClient()

    modes = []
    if args.baseline_generic:
        modes.append(("baseline-generic", run_baseline_generic))
    if args.baseline_matched:
        modes.append(("baseline-matched", run_baseline_matched))
    if args.branchout:
        modes.append(("branchout", run_branchout))
    if not modes:
        modes = [
            ("baseline-generic", run_baseline_generic),
            ("baseline-matched", run_baseline_matched),
            ("branchout", run_branchout),
        ]

    results: List[TaskResult] = []
    for mode_name, fn in modes:
        for task in tasks:
            try:
                r = await fn(task, llm)
            except Exception as e:  # noqa: BLE001 — one broken task must not sink the whole run
                print(f"  ! {task.id} ({mode_name}) crashed: {e}", file=sys.stderr)
                r = TaskResult(task.id, task.category, mode_name, 0.0, False, error=str(e))
            results.append(r)
            print(f"  {mode_name:16s} {task.id:16s} pass_rate={r.pass_rate:.2f} passed={r.passed}")

    summary = summarize(results)
    print("\n=== Summary — per mode, per category (never a single flat percentage) ===")
    print(json.dumps(summary, indent=2))
    errored = [r for r in results if r.error is not None]
    if errored:
        print(
            f"\n⚠ {len(errored)}/{len(results)} task runs crashed and were EXCLUDED from pass@1/mean_score "
            "above (see 'errored' counts and results.json's own 'error' field) — this is NOT a real score "
            "of 0%, something infrastructural failed. Fix the cause and re-run before trusting this summary.",
            file=sys.stderr,
        )

    out_path = Path(__file__).parent / "results.json"
    out_path.write_text(json.dumps([asdict(r) for r in results], indent=2))
    print(f"\nRaw results: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Branch-Out benchmark runner")
    parser.add_argument("--baseline-generic", action="store_true", help="Run the generic-prompt baseline")
    parser.add_argument("--baseline-matched", action="store_true", help="Run the matched-prompt baseline")
    parser.add_argument("--branchout", action="store_true", help="Run Branch-Out (beam_width=3)")
    parser.add_argument("--dry-run", action="store_true", help="Load and validate tasks only, no LLM calls")
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
