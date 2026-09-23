import ast
import asyncio
from pathlib import Path

import pytest

from llm_client import LLMClient
from tests.conftest import FakeLLM

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_llm_client_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        LLMClient()


def test_only_claude_client_imports_anthropic():
    offenders = []
    for path in BACKEND_DIR.rglob("*.py"):
        if "tests" in path.parts or path.name == "claude_client.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "anthropic" for a in node.names):
                offenders.append(str(path))
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "anthropic":
                offenders.append(str(path))
    assert offenders == [], f"anthropic imported outside claude_client.py: {offenders}"


def test_mocked_client_complete_returns_a_string():
    llm = FakeLLM()
    result = asyncio.run(llm.complete(messages=[{"role": "user", "content": "hi"}]))
    assert isinstance(result, str)
