"""PDF parsing with PyMuPDF / pymupdf4llm (layout mode).

Output is cached under storage/cache/parsed/<policy_id>.json keyed by file hash so re-runs never
re-parse unchanged PDFs.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import get_settings
from app.policies.registry import PolicyProfile
from app.policies.table_extract import extract_table_rows as _extract_table_rows
from app.policies.table_extract import extract_table_rows_isolated
from app.utils.logging import get_logger

log = get_logger(__name__)


class PDFParseError(RuntimeError):
    pass


@dataclass
class PageContent:
    page_number: int  # 1-based
    markdown: str
    plain_text: str
    row_text: str  # y-clustered reading rows (good for schedule-style pages)
    mode: str  # "markdown" | "rows" | "table"
    is_image_only: bool = False
    char_count: int = 0
    tables: int = 0
    table_rows: list[list[str]] | None = None  # for mode == "table": [[label, value], ...]


@dataclass
class ParsedDocument:
    policy_id: str
    file_name: str
    file_hash: str
    pages: list[PageContent] = field(default_factory=list)
    page_count: int = 0

    def flags(self) -> dict[int, str]:
        return {p.page_number: "image_only" for p in self.pages if p.is_image_only}


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def _rows_from_words(page) -> str:
    """Reconstruct reading rows from word boxes: cluster by baseline, sort by x."""
    words = page.get_text("words")  # x0,y0,x1,y1,word,block,line,wordno
    if not words:
        return ""
    words.sort(key=lambda w: (round(w[3], 0), w[0]))
    rows: list[list] = []
    tol = 3.0
    for w in words:
        if rows and abs(rows[-1][0][3] - w[3]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    lines = []
    for r in rows:
        r.sort(key=lambda w: w[0])
        parts: list[str] = []
        prev_x1 = None
        for w in r:
            if prev_x1 is not None and w[0] - prev_x1 > 25:
                parts.append(" | ")
            parts.append(w[4])
            prev_x1 = w[2]
        line = " ".join(parts).replace("  |  ", " | ").strip()
        lines.append(line)
    return "\n".join(lines)


def parse_pdf(pdf_path: Path, profile: PolicyProfile, use_cache: bool = True) -> ParsedDocument:
    import pymupdf  # noqa: WPS433

    settings = get_settings()
    if not pdf_path.exists():
        raise PDFParseError(f"Policy file missing: {pdf_path}")
    fhash = _file_hash(pdf_path)
    cache_dir = settings.cache_path / "parsed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{profile.policy_id}.json"
    if use_cache and cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            if raw.get("file_hash") == fhash:
                return ParsedDocument(
                    policy_id=raw["policy_id"],
                    file_name=raw["file_name"],
                    file_hash=fhash,
                    page_count=raw["page_count"],
                    pages=[PageContent(**p) for p in raw["pages"]],
                )
        except Exception as exc:  # pragma: no cover
            log.warning("Ignoring corrupt parse cache for %s: %s", profile.policy_id, exc)

    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        raise PDFParseError(f"Cannot open PDF {pdf_path.name}: {exc}") from exc

    # Pass 1 (plain PyMuPDF): text, word rows, table detection. Must run BEFORE pymupdf4llm, whose
    # layout module alters global PyMuPDF settings and disables find_tables results.
    base: dict[int, dict] = {}
    page_count = len(doc)
    for i, page in enumerate(doc):
        pno = i + 1
        if pno in profile.skip_pages:
            continue
        plain = page.get_text("text") or ""
        rows = _rows_from_words(page)
        table_rows = None
        if pno in profile.table_mode_pages:
            opts = profile.table_mode_pages[pno]
            strategy, ff = opts.get("strategy", "lines"), bool(opts.get("forward_fill", False))
            table_rows = _extract_table_rows(page, strategy, ff) or extract_table_rows_isolated(pdf_path, pno, strategy, ff)
        text_chars = len(re.sub(r"\s+", "", plain))
        base[pno] = {
            "plain": plain,
            "rows": rows,
            "table_rows": table_rows,
            "text_chars": text_chars,
            "is_image_only": text_chars < 40 and len(page.get_images()) > 0,
        }
    doc.close()

    # Pass 2: layout-aware markdown
    try:
        import pymupdf4llm

        md_chunks = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True)
    except Exception as exc:
        log.warning("pymupdf4llm failed for %s (%s); falling back to plain text", pdf_path.name, exc)
        md_chunks = [{"text": "", "metadata": {"page_number": i + 1}} for i in range(page_count)]

    pages: list[PageContent] = []
    for pno, b in base.items():
        md = ""
        tables = 0
        for c in md_chunks:
            if c.get("metadata", {}).get("page_number") == pno:
                md = c.get("text", "") or ""
                tables = len(c.get("tables") or [])
                break
        if pno in profile.table_mode_pages:
            mode = "table" if b["table_rows"] else "rows"
        else:
            mode = "rows" if pno in profile.text_mode_pages else "markdown"
        pages.append(
            PageContent(
                page_number=pno,
                markdown=md,
                plain_text=b["plain"],
                row_text=b["rows"],
                mode=mode,
                is_image_only=b["is_image_only"],
                char_count=b["text_chars"],
                tables=tables,
                table_rows=b["table_rows"],
            )
        )
    parsed = ParsedDocument(policy_id=profile.policy_id, file_name=pdf_path.name, file_hash=fhash, pages=pages, page_count=page_count)
    if not any(p.char_count > 0 for p in pages):
        raise PDFParseError(f"No extractable text in {pdf_path.name}; OCR is required but not configured")
    cache_file.write_text(json.dumps({**asdict(parsed)}, ensure_ascii=False), encoding="utf-8")
    return parsed
