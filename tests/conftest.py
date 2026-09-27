from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# The suite is offline and deterministic: no Gemini key (mock handlers stand in), no web research.
# Set before any `app` import so the cached Settings never sees the developer's .env key.
os.environ["GEMINI_API_KEY"] = ""
os.environ["WEB_RESEARCH_ENABLED"] = "false"
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("RERANKER_ENABLED", "false")  # keep unit tests fast; retrieval tests re-enable where needed

# Tests must never write runs, artifacts or decks into the developer's live workspace: the
# dashboard is a product surface and test runs showing up there is a bug. Work on a throwaway
# copy of `storage/` (indexes + SQLite, ~25 MB; the model cache is shared read-only via symlink).
if "STORAGE_DIR" not in os.environ:
    _tmp = Path(tempfile.mkdtemp(prefix="marsh-tests-"))
    _src = ROOT / "storage"
    _dst = _tmp / "storage"
    if _src.exists():
        shutil.copytree(_src, _dst, ignore=shutil.ignore_patterns("cache"))
        if (_src / "cache").exists():
            (_dst / "cache").symlink_to((_src / "cache").resolve(), target_is_directory=True)
    os.environ["STORAGE_DIR"] = str(_dst)
    os.environ.setdefault("OUTPUTS_DIR", str(_tmp / "outputs"))


@pytest.fixture(scope="session")
def settings():
    from app.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def policies(settings):
    from app.policies.registry import discover_policies

    return discover_policies()


@pytest.fixture(scope="session")
def parsed_docs(policies, settings):
    from app.policies.parser import parse_pdf

    return {prof.policy_id: (prof, doc, parse_pdf(settings.policies_path / doc.file_name, prof)) for prof, doc in policies}


@pytest.fixture(scope="session")
def chunked(parsed_docs):
    from app.policies.chunker import PolicyChunker

    return {pid: PolicyChunker(prof, doc.policy_name, source_document=doc.file_name).chunk_document(parsed) for pid, (prof, doc, parsed) in parsed_docs.items()}


@pytest.fixture(scope="session")
def retriever():
    from app.rag.retriever import HybridRetriever

    r = HybridRetriever()
    if not r.ready:
        pytest.skip("Retrieval indexes not built; run backend/scripts/ingest_policies.py --provider local")
    return r
