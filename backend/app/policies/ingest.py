"""Ingestion pipeline: PDFs -> parsed pages -> chunks -> embeddings -> FAISS + BM25 + SQLite metadata."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import get_settings
from app.models.policy import Chunk, PolicyDocument
from app.policies.chunker import PolicyChunker
from app.policies.parser import parse_pdf
from app.policies.registry import discover_policies
from app.rag.embeddings import get_embedder
from app.rag.lexical import BM25Index
from app.rag.metadata_store import SQLiteMetadataStore
from app.rag.retriever import reset_retriever
from app.rag.vector_store import FaissVectorStore
from app.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class IngestReport:
    policies: list[dict] = field(default_factory=list)
    total_chunks: int = 0
    embedding_model: str | None = None
    seconds: float = 0.0
    content_type_counts: dict[str, int] = field(default_factory=dict)


def ingest_policies(force_reparse: bool = False, embedding_provider: str | None = None) -> IngestReport:
    t0 = time.time()
    settings = get_settings()
    store = SQLiteMetadataStore()
    report = IngestReport()

    discovered = discover_policies()
    docs: list[PolicyDocument] = []
    all_chunks: list[Chunk] = []
    for profile, doc in discovered:
        pdf_path = settings.policies_path / doc.file_name
        parsed = parse_pdf(pdf_path, profile, use_cache=not force_reparse)
        doc.pages = parsed.page_count
        doc.page_flags = parsed.flags()
        doc.document_hash = parsed.file_hash
        chunks = PolicyChunker(profile, doc.policy_name, source_document=doc.file_name).chunk_document(parsed)
        if not chunks:
            raise RuntimeError(f"Chunking produced no chunks for {doc.policy_name}")
        docs.append(doc)
        all_chunks.extend(chunks)
        counts: dict[str, int] = {}
        for c in chunks:
            counts[c.content_type.value] = counts.get(c.content_type.value, 0) + 1
        report.policies.append({"policy_id": doc.policy_id, "policy_name": doc.policy_name, "file": doc.file_name, "pages": doc.pages, "chunks": len(chunks), "content_types": counts})
        log.info("Parsed %s: %d pages, %d chunks", doc.policy_name, doc.pages, len(chunks))

    store.clear_chunks()
    store.upsert_policies(docs)
    store.upsert_chunks(all_chunks)
    store.cache_clear("retrieval")
    store.cache_clear("extraction")

    embedder = get_embedder(embedding_provider)
    texts = [c.index_text for c in all_chunks]
    log.info("Embedding %d chunks with %s", len(texts), embedder.model_name)
    vectors = embedder.embed_documents(texts)

    vs = FaissVectorStore()
    vs.build(
        [c.chunk_id for c in all_chunks],
        vectors,
        meta={"embedding_model": embedder.model_name, "embedding_provider": "local" if "bge" in embedder.model_name.lower() else "gemini", "built_at": datetime.now(timezone.utc).isoformat()},
    )
    vs.save()

    bm = BM25Index()
    bm.build([c.chunk_id for c in all_chunks], texts)
    bm.save()

    # Human-inspectable metadata export
    settings.metadata_path.mkdir(parents=True, exist_ok=True)
    (settings.metadata_path / "policies.json").write_text(json.dumps([d.model_dump(mode="json") for d in docs], indent=2), encoding="utf-8")
    (settings.metadata_path / "chunks.jsonl").write_text("\n".join(c.model_dump_json() for c in all_chunks), encoding="utf-8")

    report.total_chunks = len(all_chunks)
    report.embedding_model = embedder.model_name
    for c in all_chunks:
        report.content_type_counts[c.content_type.value] = report.content_type_counts.get(c.content_type.value, 0) + 1
    report.seconds = round(time.time() - t0, 1)
    reset_retriever()
    return report
