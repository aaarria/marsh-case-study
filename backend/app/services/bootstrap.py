"""Startup bootstrap: make sure the local knowledge base exists (indexes + SQLite chunks).

Runs in a background thread at API startup so deploys without a pre-built `storage/` (or with an
empty SQLite volume) rebuild from the PDFs in data/policies. Routes return 503 until ready.
"""
from __future__ import annotations

import threading

from app.rag.retriever import get_retriever, reset_retriever
from app.utils.logging import get_logger

log = get_logger(__name__)
_state = {"status": "idle", "error": None}
_lock = threading.Lock()


def knowledge_base_ready() -> bool:
    try:
        r = get_retriever()
        return bool(r.ready and r.store.count_chunks() > 0 and r.vector.size() == r.store.count_chunks())
    except Exception:
        return False


def restore_metadata_from_files() -> bool:
    """Rebuild the SQLite chunk/policy tables from the committed metadata export (no re-embedding).

    Used when the vector/BM25 indexes shipped with the image but the SQLite volume is empty.
    """
    import json

    from app.config import get_settings
    from app.models.policy import Chunk, PolicyDocument
    from app.rag.metadata_store import SQLiteMetadataStore

    settings = get_settings()
    pol = settings.metadata_path / "policies.json"
    ch = settings.metadata_path / "chunks.jsonl"
    if not (pol.exists() and ch.exists()):
        return False
    store = SQLiteMetadataStore()
    docs = [PolicyDocument.model_validate(d) for d in json.loads(pol.read_text(encoding="utf-8"))]
    chunks = [Chunk.model_validate_json(line) for line in ch.read_text(encoding="utf-8").splitlines() if line.strip()]
    store.upsert_policies(docs)
    store.clear_chunks()
    store.upsert_chunks(chunks)
    log.info("Restored %s policies / %s chunks from metadata export", len(docs), len(chunks))
    return True


def ensure_knowledge_base(background: bool = True) -> None:
    if knowledge_base_ready():
        _state["status"] = "ready"
        return

    def _run():
        with _lock:
            if knowledge_base_ready():
                _state["status"] = "ready"
                return
            _state["status"] = "building"
            try:
                r = get_retriever()
                if r.ready and r.store.count_chunks() == 0 and restore_metadata_from_files():
                    reset_retriever()
                    if knowledge_base_ready():
                        _state["status"] = "ready"
                        return
                from app.policies.ingest import ingest_policies

                report = ingest_policies()
                reset_retriever()
                log.info("Knowledge base built: %s chunks (%s)", report.total_chunks, report.embedding_model)
                _state["status"] = "ready"
            except Exception as exc:
                log.exception("Knowledge base bootstrap failed")
                _state["status"] = "failed"
                _state["error"] = str(exc)[:500]

    if background:
        threading.Thread(target=_run, daemon=True, name="kb-bootstrap").start()
    else:
        _run()


def bootstrap_status() -> dict:
    return dict(_state)
