"""Rebuild the policy knowledge base from the four PDFs in data/policies.

Usage (from repo root or backend/):
    python backend/scripts/ingest_policies.py [--force] [--provider gemini|local] [--extract]

Steps: read PDFs -> extract -> structure-aware chunk -> embed -> FAISS -> BM25 -> SQLite metadata
        -> (optional --extract) structured policy facts for every feature.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.utils.logging import configure_logging, get_logger

log = get_logger("ingest")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-parse PDFs even if cached")
    parser.add_argument("--provider", choices=["gemini", "local"], default=None, help="Embedding provider override")
    parser.add_argument("--extract", action="store_true", help="Also run structured policy-fact extraction (requires GEMINI_API_KEY; runs without it using the offline heuristic)")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)

    from app.policies.ingest import ingest_policies

    try:
        report = ingest_policies(force_reparse=args.force, embedding_provider=args.provider)
    except Exception as exc:
        log.error("Ingestion failed: %s", exc)
        return 1

    print(json.dumps({"policies": report.policies, "total_chunks": report.total_chunks, "embedding_model": report.embedding_model, "content_types": report.content_type_counts, "seconds": report.seconds}, indent=2))

    if args.extract:
        from app.policies.extraction import extract_all_policies

        res = extract_all_policies(force=True)
        summary = {pid: {f: fact.coverage_status.value for f, fact in r.facts.items()} for pid, r in res.items()}
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
