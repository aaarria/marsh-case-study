"""Structure-aware chunking of parsed policy brochures.

Produces parent/child chunks:
  section (parent)  -> clause / list_item (children)
  table (parent)    -> table_row (children, rendered "Label: Value" with header context)
  condition chunks  -> footnotes / T&C segments, linked to referencing clauses via markers
Classifies content_type (exclusion, waiting_period, eligibility, definition, add_on, discount,
pricing, marketing_stat) from section context and deterministic keyword rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.policy import Chunk, ContentType
from app.policies.parser import PageContent, ParsedDocument
from app.policies.registry import PolicyProfile
from app.utils.ids import stable_id

SUP_RE = re.compile(r"<sup>(.*?)</sup>", re.DOTALL)
MARK_RE = re.compile(r"</?mark>")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
BR_RE = re.compile(r"<br\s*/?>")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}")
BULLET_RE = re.compile(r"^\s*(?:[-•*]|\d+\.)\s+(.*)$")
# trailing symbol markers on words: e.g. "Benefit*", "Benefit**", "policy~~", "network##", "°°"
# (only after letters/closing parens so "50%" and "INR 5,00,000" are preserved)
TRAILING_SYM_RE = re.compile(r"(?<=[A-Za-z\)])([\*\^~#°@%\$]{1,3})(?=\s|$|[,.;:)])")
LEADING_NUM_MARKER_RE = re.compile(r"^\((\d{1,2})\)\s*")
# footnote segment starters: symbol markers, (n), or bare n glued to capital letter, or ⟦n⟧ tokens
FOOTNOTE_START_RE = re.compile(
    r"(?:(?<=^)|(?<=[\.\!\?])|(?<=[\.\!\?]\s)|(?<=\s{2}))(?P<m>\(\d{1,2}\)|⟦[^⟧]+⟧|[\*\^~#°@!\$]{1,3}|\d{1,2}(?=[A-Z]))"
)
LIGATURES = {"\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\ufb03": "ffi", "\ufb04": "ffl"}

KEYWORDS = {
    ContentType.EXCLUSION: re.compile(r"\b(exclusions?|excluded|not covered|not payable)\b", re.I),
    ContentType.WAITING_PERIOD: re.compile(r"\b(wait(?:ing)? period|waiting|pre-existing|PED)\b", re.I),
    ContentType.ELIGIBILITY: re.compile(r"\b(entry age|exit age|eligib|age of proposer|who are covered|relationship|cover type|family floater|individual policy|maximum of \d+ adults)", re.I),
    ContentType.DEFINITION: re.compile(r"\b(is an amount you|means that|refers to|is defined|here means)\b", re.I),
    ContentType.DISCOUNT: re.compile(r"\bdiscount", re.I),
    ContentType.PRICING: re.compile(r"\b(premium|zone \d|pricing|instal?ment|tenure)\b", re.I),
    ContentType.ADD_ON: re.compile(r"\b(add-?on|optional benefit|optional cover|rider|additional premium|extra premium)\b", re.I),
}


def _clean(text: str) -> str:
    text = COMMENT_RE.sub(" ", text)
    text = SUP_RE.sub(lambda m: "⟦" + re.sub(r"[\*\s]", "", m.group(1)) + "⟧", text)
    text = MARK_RE.sub("", text)
    text = BR_RE.sub(" ", text)
    text = BOLD_RE.sub(r"\1", text)
    text = text.replace("`", "INR ")  # rupee glyph rendered as backtick in these brochures
    for lig, rep in LIGATURES.items():
        text = text.replace(lig, rep)
    text = re.sub(r"INR\s+INR", "INR", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _strip_markers(text: str) -> tuple[str, list[str]]:
    """Return text without marker tokens plus the list of markers found."""
    markers: list[str] = []
    lead = LEADING_NUM_MARKER_RE.match(text)
    if lead:
        markers.append(lead.group(1))
        text = text[lead.end():]
    for m in re.finditer(r"⟦([^⟧]+)⟧", text):
        markers.append(m.group(1).strip())
    text = re.sub(r"⟦[^⟧]+⟧", "", text)
    for m in TRAILING_SYM_RE.finditer(text):
        markers.append(m.group(1))
    text = TRAILING_SYM_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    norm = []
    for mk in markers:
        mk = mk.strip("() ")
        if mk and mk not in norm:
            norm.append(mk)
    return text, norm


@dataclass
class _Block:
    kind: str  # heading | table | bullet | para
    text: str
    level: int = 0
    rows: list[list[str]] | None = None


def _blocks_from_markdown(md: str) -> list[_Block]:
    lines = md.splitlines()
    blocks: list[_Block] = []
    i = 0
    para: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            text = " ".join(s.strip() for s in para).strip()
            if text:
                blocks.append(_Block("para", text))
            para = []

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush_para()
            i += 1
            continue
        hm = HEADING_RE.match(line.strip())
        if hm:
            flush_para()
            blocks.append(_Block("heading", hm.group(2).strip(), level=len(hm.group(1))))
            i += 1
            continue
        if TABLE_LINE_RE.match(line):
            flush_para()
            rows: list[list[str]] = []
            while i < len(lines) and TABLE_LINE_RE.match(lines[i]):
                if not TABLE_SEP_RE.match(lines[i].strip().strip("|").strip()):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append(_Block("table", "", rows=rows))
            continue
        bm = BULLET_RE.match(line)
        if bm:
            flush_para()
            blocks.append(_Block("bullet", bm.group(1).strip()))
            i += 1
            continue
        para.append(line)
        i += 1
    flush_para()
    return blocks


def _blocks_from_rows(row_text: str) -> list[_Block]:
    blocks: list[_Block] = []
    for line in row_text.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"\d{1,2}", line):
            continue
        if " | " in line:
            cells = [c.strip() for c in line.split(" | ")]
            blocks.append(_Block("table", "", rows=[cells]))
        else:
            blocks.append(_Block("para", line))
    return blocks


class PolicyChunker:
    def __init__(self, profile: PolicyProfile, policy_name: str):
        self.profile = profile
        self.policy_name = policy_name

    # ---------- classification ----------
    def _classify(self, text: str, section: str | None, section_kind: str | None, default: ContentType) -> ContentType:
        if self.profile.is_marketing_stat(text) or section_kind == "marketing_stats":
            return ContentType.MARKETING_STAT
        if re.search(r"Section 41 of Insurance Act|Prohibition of Rebates", text, re.I) or section_kind == "legal":
            return ContentType.CONDITION
        if section_kind == "exclusions" or (section and re.search(r"exclusion", section, re.I)):
            if not re.search(r"\bwait(?:ing)? period", text, re.I):
                return ContentType.EXCLUSION
        if section_kind == "waiting_periods":
            return ContentType.WAITING_PERIOD
        if section_kind == "conditions":
            if re.match(r"\s*waiting period", text, re.I):
                return ContentType.WAITING_PERIOD
            return ContentType.CONDITION
        if section_kind in {"add_ons", "optional_benefits"} or self._mentions_add_on(text):
            return ContentType.ADD_ON
        if re.search(r"\bwait(?:ing)?\s*period|zero waiting", text, re.I):
            return ContentType.WAITING_PERIOD
        if section_kind == "eligibility" or KEYWORDS[ContentType.ELIGIBILITY].search(text):
            return ContentType.ELIGIBILITY
        if KEYWORDS[ContentType.EXCLUSION].search(text) and not re.search(r"\b(covered|coverage)\b", text, re.I):
            return ContentType.EXCLUSION
        if KEYWORDS[ContentType.ADD_ON].search(text):
            return ContentType.ADD_ON
        if section_kind in {"deductible", "discounts"} or KEYWORDS[ContentType.DISCOUNT].search(text):
            return ContentType.DISCOUNT
        if KEYWORDS[ContentType.PRICING].search(text):
            return ContentType.PRICING
        if KEYWORDS[ContentType.DEFINITION].search(text):
            return ContentType.DEFINITION
        return default

    def _mentions_add_on(self, text: str) -> bool:
        head = text[:80]
        return any(name.lower() in head.lower() for name in self.profile.add_on_names)

    # ---------- main ----------
    def chunk_document(self, parsed: ParsedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        for page in parsed.pages:
            if page.is_image_only:
                continue
            chunks.extend(self._chunk_page(page))
        self._link_footnotes(chunks)
        return chunks

    def _new_chunk(
        self,
        page: int,
        seq: int,
        text: str,
        content_type: ContentType,
        section: str | None,
        subsection: str | None,
        parent: str | None,
        markers: list[str],
        meta: dict | None = None,
    ) -> Chunk:
        pid = self.profile.policy_id
        cid = stable_id(pid, str(page), str(seq), text[:64])
        ctx = " | ".join(x for x in [self.policy_name, section, subsection] if x)
        scope = self.profile.variant_scope(" ".join(x for x in [section or "", subsection or "", text] if x))
        m = dict(meta or {})
        if scope:
            m["variant_scope"] = scope
        return Chunk(
            chunk_id=f"{pid}:{cid}",
            policy_id=pid,
            policy_name=self.policy_name,
            page_number=page,
            section=section,
            subsection=subsection,
            clause=f"p{page}.{seq}",
            content_type=content_type,
            parent_chunk_id=parent,
            source_text=text,
            search_text=f"{ctx} :: {text}" if ctx else text,
            footnote_markers=markers,
            meta=m,
        )

    def _chunk_page(self, page: PageContent) -> list[Chunk]:
        if page.mode == "table" and page.table_rows:
            title_block = []
            first_line = (page.plain_text or "").strip().splitlines()
            if first_line:
                title_block = [_Block("heading", first_line[0].strip(), level=1)]
            blocks = title_block + [_Block("table", "", rows=[list(r) for r in page.table_rows])]
        elif page.mode == "rows":
            blocks = _blocks_from_rows(page.row_text)
        else:
            blocks = _blocks_from_markdown(page.markdown)
        out: list[Chunk] = []
        section: str | None = None
        subsection: str | None = None
        section_kind: str | None = None
        section_parent: str | None = None
        seq = 0

        def next_seq() -> int:
            nonlocal seq
            seq += 1
            return seq

        if page.mode in {"rows", "table"}:
            section = "Schedule of Key Benefits"
            section_kind = "schedule"
        default_section = self.profile.page_default_sections.get(page.page_number)
        if default_section and section is None:
            section = default_section
            section_kind = self.profile.section_kind(default_section)

        for b in blocks:
            if b.kind == "heading":
                title, _ = _strip_markers(_clean(b.text))
                if not title or len(title) < 3:
                    continue
                kind = self.profile.section_kind(title)
                if b.level <= 2 or section is None or kind:
                    section, subsection, section_kind = title, None, kind or section_kind if b.level > 2 else kind
                    if b.level <= 2:
                        section_kind = kind
                    parent = self._new_chunk(page.page_number, next_seq(), title, ContentType.SECTION, section, None, None, [], {"heading_level": b.level})
                    out.append(parent)
                    section_parent = parent.chunk_id
                else:
                    subsection = title
                continue

            if b.kind == "table" and b.rows:
                out.extend(self._chunk_table(page.page_number, b.rows, section, subsection, section_kind, section_parent, next_seq))
                continue

            text = _clean(b.text)
            if not text or len(text) < 3:
                continue
            # footnote / T&C block detection
            if self._looks_like_footnotes(text):
                out.extend(self._chunk_footnotes(page.page_number, text, section, section_parent, next_seq))
                continue
            clean_text, markers = _strip_markers(text)
            if len(clean_text) < 3:
                continue
            default = ContentType.LIST_ITEM if b.kind == "bullet" else ContentType.CLAUSE
            ctype = self._classify(clean_text, section, section_kind, default)
            # Pipe-delimited exclusion / waiting-period lists: one parent + one child per item
            if ctype in {ContentType.EXCLUSION, ContentType.WAITING_PERIOD} and clean_text.count("|") >= 2:
                parent = self._new_chunk(page.page_number, next_seq(), clean_text, ctype, section, subsection, section_parent, markers, {"list": True})
                out.append(parent)
                for item in [x.strip() for x in clean_text.split("|") if x.strip()]:
                    out.append(self._new_chunk(page.page_number, next_seq(), item, ctype, section, subsection, parent.chunk_id, markers, {"list_item": True}))
                continue
            # Long paragraphs: split into sentences groups of ~600 chars while keeping parent
            if len(clean_text) > 900:
                parent = self._new_chunk(page.page_number, next_seq(), clean_text, ctype, section, subsection, section_parent, markers)
                out.append(parent)
                for piece in _split_sentences(clean_text, 500):
                    out.append(self._new_chunk(page.page_number, next_seq(), piece, ctype, section, subsection, parent.chunk_id, markers))
            else:
                out.append(self._new_chunk(page.page_number, next_seq(), clean_text, ctype, section, subsection, section_parent, markers))
        return out

    def _chunk_table(self, page: int, rows: list[list[str]], section, subsection, section_kind, section_parent, next_seq) -> list[Chunk]:
        out: list[Chunk] = []
        clean_rows: list[list[str]] = []
        for r in rows:
            cells = [_clean(c) for c in r]
            if any(c for c in cells):
                clean_rows.append(cells)
        if not clean_rows:
            return out
        ncols = max(len(r) for r in clean_rows)
        # header detection: first row with >2 cols where subsequent rows are numeric-ish
        header: list[str] | None = None
        if ncols >= 3:
            header = clean_rows[0]
            body = clean_rows[1:]
            # HDFC deductible table: second row holds real column headers
            if body and sum(1 for c in body[0] if not re.search(r"\d", c)) >= len(body[0]) - 1:
                header = [h or b for h, b in zip(header + [""] * ncols, body[0] + [""] * ncols)][:ncols]
                body = body[1:]
        else:
            body = clean_rows
        table_md = "\n".join(" | ".join(r) for r in clean_rows)
        table_text, table_markers = _strip_markers(table_md)
        parent = self._new_chunk(page, next_seq(), table_text, ContentType.TABLE, section, subsection, section_parent, table_markers, {"rows": len(clean_rows), "cols": ncols})
        out.append(parent)
        last_label: str | None = None
        for r in body:
            r = r + [""] * (ncols - len(r))
            if ncols == 2:
                label, value = r[0], r[1]
                if not label and last_label:
                    label = last_label
                if not value and label:
                    # label-only row (e.g. "Benefits", "Optional Benefits") acts as a subsection
                    continue
                last_label = label or last_label
                row_text = f"{label}: {value}" if label else value
            else:
                parts = []
                row_label = r[0]
                for j, cell in enumerate(r):
                    if j == 0 or not cell:
                        continue
                    col = header[j] if header and j < len(header) and header[j] else f"col{j}"
                    parts.append(f"{col} = {cell}")
                row_text = f"{row_label}: " + "; ".join(parts) if parts else row_label
            row_clean, markers = _strip_markers(row_text)
            if len(row_clean) < 3:
                continue
            ctype = self._classify(row_clean, section, section_kind, ContentType.TABLE_ROW)
            if ctype in {ContentType.CLAUSE, ContentType.LIST_ITEM}:
                ctype = ContentType.TABLE_ROW
            if section_kind in {"optional_benefits", "add_ons"}:
                ctype = ContentType.ADD_ON
            out.append(self._new_chunk(page, next_seq(), row_clean, ctype, section, subsection, parent.chunk_id, markers, {"row_label": (r[0] or last_label or "")}))
        return out

    @staticmethod
    def _looks_like_footnotes(text: str) -> bool:
        starts = [m for m in FOOTNOTE_START_RE.finditer(text)]
        lead = re.match(r"^\s*(\(\d{1,2}\)|⟦[^⟧]+⟧|[\*\^~#°@!%\$]{1,3}|\d{1,2}(?=[A-Z]))", text) is not None
        return (len(starts) >= 3 and len(text) > 200) or (lead and len(starts) >= 2 and len(text) > 120)

    def _chunk_footnotes(self, page: int, text: str, section, section_parent, next_seq) -> list[Chunk]:
        out: list[Chunk] = []
        positions = [m.start("m") for m in FOOTNOTE_START_RE.finditer(text)]
        if not positions or positions[0] > 0:
            positions = [0] + positions
        positions = sorted(set(positions))
        segments = [text[a:b].strip() for a, b in zip(positions, positions[1:] + [len(text)])]
        parent_text, _ = _strip_markers(text)
        parent = self._new_chunk(page, next_seq(), parent_text, ContentType.CONDITION, section or "Terms and conditions", None, section_parent, [], {"footnote_block": True})
        out.append(parent)
        for seg in segments:
            if len(seg) < 12:
                continue
            m = re.match(r"^(\(\d{1,2}\)|⟦[^⟧]+⟧|[\*\^~#°@!%\$]{1,3}|\d{1,2}(?=[A-Z]))\s*", seg)
            marker = None
            body = seg
            if m:
                marker = m.group(1).strip("()⟦⟧ ")
                body = seg[m.end():].strip()
            body_clean, extra = _strip_markers(body)
            if len(body_clean) < 12:
                continue
            markers = [marker] if marker else []
            ctype = ContentType.CONDITION
            if KEYWORDS[ContentType.EXCLUSION].search(body_clean) and not re.search(r"covered", body_clean, re.I):
                ctype = ContentType.EXCLUSION
            out.append(self._new_chunk(page, next_seq(), body_clean, ctype, section or "Terms and conditions", None, parent.chunk_id, markers, {"footnote_marker": marker}))
        return out

    def _link_footnotes(self, chunks: list[Chunk]) -> None:
        """Attach footnote/condition chunk ids to clauses carrying the same marker (same doc, nearest page)."""
        footnotes = [c for c in chunks if c.meta.get("footnote_marker")]
        by_marker: dict[str, list[Chunk]] = {}
        for f in footnotes:
            by_marker.setdefault(f.meta["footnote_marker"], []).append(f)
        for c in chunks:
            if c.meta.get("footnote_marker") or not c.footnote_markers:
                continue
            refs: list[str] = []
            for mk in c.footnote_markers:
                cands = by_marker.get(mk) or []
                if not cands:
                    continue
                best = min(cands, key=lambda f: abs(f.page_number - c.page_number))
                if best.chunk_id not in refs:
                    refs.append(best.chunk_id)
            c.footnote_refs = refs


def _split_sentences(text: str, target: int) -> list[str]:
    sentences = re.split(r"(?<=[\.\!\?])\s+(?=[A-Z0-9])", text)
    pieces: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) + 1 > target and cur:
            pieces.append(cur.strip())
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        pieces.append(cur.strip())
    return pieces
