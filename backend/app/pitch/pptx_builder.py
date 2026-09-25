"""python-pptx export: the client deck in Marsh's own template, plus the markdown audit report.

Brand source: the Marsh case-study brief (docs/) is itself a Marsh-branded deck, so its measured
geometry, palette and type are the template here.
  Cover      sky #A7E2F0 field, navy MarshMcLennan wordmark top-left, Georgia title, right-aligned meta,
             ocean hero image.
  Content    navy #002C77 header band (0.9") with a Georgia title and the white wordmark top-right;
             Calibri body in navy with en-dash bullets and a hanging indent; optional two columns with
             bold Calibri headers ruled in navy and a hairline divider; footer rule, navy wordmark
             bottom-left, copyright and page number bottom-right.
Fonts: Georgia (the brief uses Georgia Pro Light; plain Georgia ships with Office and macOS) and Calibri.

Layout is measured, not hoped for: body text is sized to fit the space left above the references,
the references block is sized from its own line count and sits above the footer, and nothing is
allowed to overlap. Text metrics are estimates (Calibri average advance), padded on the safe side.

Every reference is a live hyperlink: brochure citations open the PDF at the cited page through the
API (PUBLIC_API_URL/api/policies/{id}/document#page=N) and company facts open the web page they were
read from. The superscript markers in the bullets link to the same targets.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from app.config import get_settings
from app.models.pitch import AuditReport, Pitch, Slide
from app.models.policy import SourceRef

# Marsh logo palette: navy, ocean, cyan, sky.
NAVY = RGBColor(0x00, 0x2C, 0x77)
OCEAN = RGBColor(0x01, 0x6D, 0x9E)
SKY = RGBColor(0xA7, 0xE2, 0xF0)
TEAL = RGBColor(0x00, 0xAB, 0xC7)
SLATE = RGBColor(0x64, 0x74, 0x8B)
HAIRLINE = RGBColor(0xCB, 0xD5, 0xE1)
CANVAS = RGBColor(0xF4, 0xF7, 0xFB)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

SERIF = "Georgia"
SANS = "Calibri"

W, H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.5)
CONTENT_W = W - 2 * MARGIN
HEADER_H = Inches(0.9)
FOOTER_Y = Inches(7.1)

ASSETS = Path(__file__).parent / "assets"
LOGO_WHITE = ASSETS / "marsh-white.png"
LOGO_NAVY = ASSETS / "marsh-navy.png"
COVER_IMAGE = ASSETS / "cover.jpg"

# Text metrics used to size blocks before they are drawn (inches per point of font size).
# Calibri’s average advance for mixed English is ~0.45 em; 0.48 leaves slack for wide words.
CHAR_W = 0.48 / 72
LINE_H = 1.22 / 72
BODY_SIZES = (16, 15, 14, 13, 12)
PARA_GAP_PT = 6
FOOTNOTE_PT = 9

# Bullet kinds that read as "our view" versus labelled assumptions; drives the two-column layout.
KIND_COLUMN = {"company": "left", "policy": "left", "recommendation": "left", "marsh": "left", "assumption": "right"}
LEFT_HEADERS = {"company": "What we understand", "policy": "Key benefits", "recommendation": "Our view", "marsh": "Why Marsh"}
RIGHT_HEADER = "Assumptions to confirm"


# --------------------------------------------------------------------------- primitives
def _prs() -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    return prs


def _rect(slide, x, y, w, h, fill: RGBColor):
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _card(slide, x, y, w, h):
    """White panel with a hairline border, as on the brief's challenge slides."""
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shp.fill.solid()
    shp.fill.fore_color.rgb = WHITE
    shp.line.color.rgb = HAIRLINE
    shp.line.width = Pt(1)
    shp.shadow.inherit = False
    return shp


def _line(slide, x1, y1, x2, y2, color: RGBColor, width_pt: float):
    ln = slide.shapes.add_connector(1, x1, y1, x2, y2)  # 1 = straight
    ln.line.color.rgb = color
    ln.line.width = Pt(width_pt)
    return ln


def _textbox(slide, x, y, w, h, *, anchor=MSO_ANCHOR.TOP, wrap=True):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    return tb


def _run(p, text: str, size: float, color: RGBColor, *, bold=False, italic=False, font=SANS, superscript=False, link: str | None = None):
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    r.font.name = font
    if superscript:
        r.font._element.set("baseline", "30000")
    if link:
        r.hyperlink.address = link
    return r


def _text(slide, x, y, w, h, text: str, size: float, color: RGBColor = NAVY, *, bold=False, italic=False, align=PP_ALIGN.LEFT, font=SANS, anchor=MSO_ANCHOR.TOP):
    tb = _textbox(slide, x, y, w, h, anchor=anchor)
    p = tb.text_frame.paragraphs[0]
    p.alignment = align
    _run(p, text, size, color, bold=bold, italic=italic, font=font)
    return tb


def _bullet_para(p, indent_in: float):
    """Real bullet formatting (en dash, hanging indent) rather than a literal character in the text."""
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(int(Inches(indent_in))))
    pPr.set("indent", str(-int(Inches(indent_in))))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum", "a:buFont"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    bu_font = pPr.makeelement(qn("a:buFont"), {"typeface": SANS})
    bu_char = pPr.makeelement(qn("a:buChar"), {"char": "\u2013"})
    pPr.append(bu_font)
    pPr.append(bu_char)


def _logo(slide, path: Path, x, y, width):
    return slide.shapes.add_picture(str(path), x, y, width=width)


# --------------------------------------------------------------------------- measurement
def _lines(text: str, size_pt: float, width_in: float) -> int:
    per_line = max(8, int(width_in / (CHAR_W * size_pt)))
    n = 0
    for para in text.split("\n"):
        n += max(1, math.ceil(len(para) / per_line))
    return n


def _block_height_in(items: list[str], size_pt: float, width_in: float, gap_pt: float = PARA_GAP_PT) -> float:
    lines = sum(_lines(t, size_pt, width_in) for t in items)
    return lines * size_pt * LINE_H + max(0, len(items) - 1) * gap_pt / 72


# --------------------------------------------------------------------------- slide furniture
def _header(slide, title: str, page: str):
    _rect(slide, 0, 0, W, HEADER_H, NAVY)
    _text(slide, MARGIN, 0, W - Inches(3.7), HEADER_H, title, 20, WHITE, font=SERIF, anchor=MSO_ANCHOR.MIDDLE)
    _logo(slide, LOGO_WHITE, W - Inches(2.9), Inches(0.35), Inches(2.4))  # 2.4" wide -> 0.2" tall, centred in the band
    del page  # page numbers live in the footer, as in the brief


def _footer(slide, page: int, total: int, year: int, note: str | None = None):
    _line(slide, 0, FOOTER_Y, W, FOOTER_Y, NAVY, 1.0)
    _logo(slide, LOGO_NAVY, Inches(0.4), Inches(7.23), Inches(1.9))  # 1.9" wide -> 0.16" tall
    if note:
        _text(slide, Inches(2.5), Inches(7.16), Inches(6.7), Inches(0.3), note, 7.5, SLATE, anchor=MSO_ANCHOR.MIDDLE)
    _text(slide, Inches(9.3), Inches(7.16), Inches(3.2), Inches(0.3), f"Copyright \u00a9 {year} Marsh. All rights reserved.", FOOTNOTE_PT, NAVY, align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
    _text(slide, Inches(12.6), Inches(7.16), Inches(0.43), Inches(0.3), str(page), FOOTNOTE_PT, NAVY, align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
    del total


Reference = tuple[str, str | None]  # (display text, hyperlink)


def _cite(ref: SourceRef) -> Reference:
    """Brochure citation, linked to the PDF opened at the cited page via the API."""
    loc = f"p.{ref.page}"
    if ref.section:
        loc += f", {ref.section[:48]}"
    if ref.clause:
        loc += f" ({ref.clause[:30]})"
    link = f"{get_settings().public_api_url.rstrip('/')}/api/policies/{ref.policy_id}/document#page={ref.page}"
    return f"{ref.policy_name or ref.policy_id} brochure, {loc}", link


def _cite_web(url: str) -> Reference:
    """Web citation shown as host + readable path, linked to the page itself."""
    u = urlparse(url)
    host = u.netloc.removeprefix("www.")
    path = unquote(u.path).strip("/").replace("_", " ").replace("-", " ")
    if len(path) > 60:
        path = path[:57] + "…"
    return f"{host}" + (f" · {path}" if path else ""), url


def _references(slide, refs: list[Reference], bottom, x, width) -> int:
    """Numbered references above the footer, each a hyperlink. Two columns past four entries. Returns the block's top (EMU)."""
    if not refs:
        return bottom
    cols = 2 if len(refs) > 4 else 1
    col_w = (width - Inches(0.3) * (cols - 1)) / cols
    col_w_in = Emu(col_w).inches
    rows = math.ceil(len(refs) / cols)
    col_items = [refs[i * rows : (i + 1) * rows] for i in range(cols)]
    height_in = max(_block_height_in([f"{i}. {t}" for i, (t, _) in enumerate(ci, 1)], FOOTNOTE_PT, col_w_in, gap_pt=2) for ci in col_items if ci)
    label_h = Inches(0.2)
    top = bottom - Inches(height_in) - label_h
    _text(slide, x, top, width, label_h, "Sources", 8, SLATE, bold=True)
    for c, items in enumerate(col_items):
        if not items:
            continue
        tb = _textbox(slide, x + c * (col_w + Inches(0.3)), top + label_h, col_w, Inches(height_in))
        tf = tb.text_frame
        for k, (text, link) in enumerate(items):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            p.space_after = Pt(2)
            _run(p, f"{c * rows + k + 1}. ", FOOTNOTE_PT, SLATE)
            _run(p, text, FOOTNOTE_PT, SLATE, link=link)
    return top


def _body_column(slide, x, y, w, items: list[tuple[str, list[tuple[str, str | None]], str]], size: float, header: str | None):
    """One column of en-dash bullets. `items` are (text, [(marker number, link)], kind)."""
    top = y
    if header:
        _text(slide, x + Inches(0.1), top, w - Inches(0.1), Inches(0.3), header, 16, NAVY, bold=True)
        _line(slide, x, top + Inches(0.36), x + w, top + Inches(0.36), NAVY, 1.5)
        top += Inches(0.6)
    tb = _textbox(slide, x + Inches(0.1), top, w - Inches(0.1), Inches(0.5))
    tf = tb.text_frame
    for k, (text, markers, kind) in enumerate(items):
        p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
        p.space_after = Pt(PARA_GAP_PT)
        _bullet_para(p, 0.37)
        if text.lower().startswith("watch-out:") and header == "What to watch":
            text = text[len("watch-out:") :].lstrip()
        if kind == "assumption":
            label, rest = "Assumption: ", text[len("assumption:") :].lstrip() if text.lower().startswith("assumption:") else text
            _run(p, label, size, SLATE, bold=True)
            _run(p, rest, size, NAVY)
        else:
            _run(p, text, size, NAVY)
        for m, (num, link) in enumerate(markers):
            # Each marker is its own run so it can carry its own hyperlink: "¹˒²" -> two links.
            _run(p, (" " if m == 0 else ",") + num, size, TEAL, superscript=True, link=link)
    return tb


def _plan_columns(slide: Slide) -> tuple[list, list, str | None, str | None]:
    """Split a slide into (left, right, left_header, right_header), in the brief's two-column layout.

    Right column = labelled assumptions when the slide mixes statements and assumptions, or the
    "Watch-out:" items when a comparison slide carries at least two of them. Otherwise one column.
    """
    watch = [b for b in slide.bullets if b.text.lower().startswith("watch-out")]
    if len(watch) >= 2 and len(watch) < len(slide.bullets):
        rest = [b for b in slide.bullets if b not in watch]
        return rest, watch, "How it compares", "What to watch"
    left = [b for b in slide.bullets if KIND_COLUMN.get(b.kind, "left") == "left"]
    right = [b for b in slide.bullets if KIND_COLUMN.get(b.kind) == "right"]
    if not left or not right:
        return slide.bullets, [], None, None
    kinds = [b.kind for b in left]
    order = ("recommendation", "policy", "company", "marsh")
    lead = max(set(kinds), key=lambda k: (kinds.count(k), -order.index(k) if k in order else -9))
    return left, right, LEFT_HEADERS.get(lead, "Our view"), RIGHT_HEADER


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    if text.lower().startswith("assumption:"):
        text = text.split(":", 1)[1].strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _draw_exposure_map(slide_shape, slide: Slide, markers: dict[int, list[tuple[str, str | None]]], top, bottom) -> None:
    """Slide 3 of the deck: exposures on the left, brochure benefits on the right, one why-line.

    Policy text in the boxes is the audited bullet, clipped only for the shape. Citation markers
    stay on the benefit cards so a figure can still be opened in the brochure.
    """
    indexed = list(enumerate(slide.bullets))
    exposures = [(i, b) for i, b in indexed if b.kind in {"company", "assumption"}][:3]
    benefits = [(i, b) for i, b in indexed if b.kind == "policy"][:3]
    why = next((b for _, b in indexed if b.kind in {"recommendation", "marsh"}), None)
    rows = max(len(exposures), len(benefits), 1)
    why_h = Inches(0.62) if why else Inches(0)
    gap = Inches(0.1)
    region_bottom = bottom - why_h - (Inches(0.12) if why else 0)
    chrome = Inches(0.3) + (Inches(0.34) if slide.subtitle else 0)
    avail = region_bottom - top - chrome
    row_h = min(Inches(1.15), (avail - gap * (rows - 1)) / rows)
    if row_h < Inches(0.7):
        row_h = Inches(0.7)

    if slide.subtitle:
        _text(slide_shape, MARGIN, top, CONTENT_W, Inches(0.28), slide.subtitle, 12, SLATE)
        top += Inches(0.34)

    left_x, left_w = MARGIN, Inches(5.35)
    arrow_w = Inches(0.42)
    right_x = left_x + left_w + Inches(0.7)
    right_w = W - MARGIN - right_x
    _text(slide_shape, left_x, top, left_w, Inches(0.24), "Client exposure", 11, OCEAN, bold=True)
    _text(slide_shape, right_x, top, right_w, Inches(0.24), "Stated in the brochure", 11, OCEAN, bold=True)
    top += Inches(0.3)

    for r in range(rows):
        y = top + r * (row_h + gap)
        if r < len(exposures):
            i, b = exposures[r]
            _card(slide_shape, left_x, y, left_w, row_h)
            _rect(slide_shape, left_x, y, Inches(0.08), row_h, SKY)
            label = "Assumption" if b.kind == "assumption" else "From the profile"
            _text(slide_shape, left_x + Inches(0.22), y + Inches(0.08), left_w - Inches(0.34), Inches(0.2), label, 9, SLATE, bold=True)
            _text(slide_shape, left_x + Inches(0.22), y + Inches(0.28), left_w - Inches(0.34), row_h - Inches(0.36), _short(b.text), 12, NAVY)
        if r < len(benefits):
            i, b = benefits[r]
            _card(slide_shape, right_x, y, right_w, row_h)
            _rect(slide_shape, right_x, y, Inches(0.08), row_h, TEAL)
            _text(slide_shape, right_x + Inches(0.22), y + Inches(0.08), right_w - Inches(0.34), Inches(0.2), "Brochure benefit", 9, TEAL, bold=True)
            tb = _textbox(slide_shape, right_x + Inches(0.22), y + Inches(0.28), right_w - Inches(0.34), row_h - Inches(0.36))
            p = tb.text_frame.paragraphs[0]
            _run(p, _short(b.text), 12, NAVY)
            for m, (num, link) in enumerate(markers.get(i, [])):
                _run(p, (" " if m == 0 else ",") + num, 12, TEAL, superscript=True, link=link)
        if r < len(exposures) and r < len(benefits):
            mid_y = y + row_h / 2 - Inches(0.11)
            arrow_x = left_x + left_w + Inches(0.14)
            shp = slide_shape.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, arrow_x, mid_y, arrow_w, Inches(0.22))
            shp.fill.solid()
            shp.fill.fore_color.rgb = OCEAN
            shp.line.fill.background()
            shp.shadow.inherit = False

    if why:
        bar_y = top + rows * (row_h + gap) + Inches(0.06)
        if bar_y + why_h > bottom:
            bar_y = bottom - why_h
        _rect(slide_shape, MARGIN, bar_y, CONTENT_W, why_h, NAVY)
        _text(slide_shape, MARGIN + Inches(0.2), bar_y, CONTENT_W - Inches(0.4), why_h, _short(why.text, 180), 13, WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)


def _fit(items_left: list[str], items_right: list[str], width_left_in: float, width_right_in: float, avail_in: float, header_in: float) -> float | None:
    for size in BODY_SIZES:
        hl = header_in + _block_height_in(items_left, size, width_left_in)
        hr = header_in + _block_height_in(items_right, size, width_right_in) if items_right else 0
        if max(hl, hr) <= avail_in:
            return size
    return None


# --------------------------------------------------------------------------- deck
def build_pitch_deck(pitch: Pitch, refs_by_chunk: dict[str, SourceRef], out_path: Path, audit: AuditReport | None = None) -> Path:
    prs = _prs()
    blank = prs.slide_layouts[6]
    total = len(pitch.slides) + 1
    now = datetime.now(timezone.utc)

    # ---- cover: the brief's title page
    s = prs.slides.add_slide(blank)
    _rect(s, 0, 0, W, H, SKY)
    _logo(s, LOGO_NAVY, Inches(0.4), Inches(0.38), Inches(3.2))  # 3.2" wide -> 0.26" tall
    _text(s, Inches(11.4), Inches(0.35), Inches(1.5), Inches(0.25), str(now.year), FOOTNOTE_PT, NAVY, align=PP_ALIGN.RIGHT)
    _text(s, MARGIN, Inches(2.0), Inches(9.4), Inches(1.5), f"Employee health cover for {pitch.company_name}", 36, NAVY, font=SERIF, anchor=MSO_ANCHOR.BOTTOM)
    meta = [f"Prepared by Marsh \u00b7 {now.strftime('%B %Y')}", f"Draft v{pitch.version}, for advisor review"]
    if audit:
        meta.append(f"Evidence audit: {audit.summary.gate}")
    tb = _textbox(s, Inches(9.9), Inches(2.6), Inches(3.0), Inches(0.9), anchor=MSO_ANCHOR.BOTTOM)
    for k, line in enumerate(meta):
        p = tb.text_frame.paragraphs[0] if k == 0 else tb.text_frame.add_paragraph()
        p.alignment = PP_ALIGN.RIGHT
        _run(p, line, 12, NAVY)
    hero_w, hero_h = Inches(12.5), Inches(3.4)
    pic = s.shapes.add_picture(str(COVER_IMAGE), Inches(0.4), Inches(3.85), width=hero_w, height=hero_h)
    # Crop the ocean image to the hero's aspect instead of stretching it.
    img_aspect = pic.image.size[0] / pic.image.size[1]
    box_aspect = hero_w / hero_h
    if img_aspect < box_aspect:
        keep = img_aspect / box_aspect
        pic.crop_top = pic.crop_bottom = (1 - keep) / 2
    else:
        keep = box_aspect / img_aspect
        pic.crop_left = pic.crop_right = (1 - keep) / 2

    # ---- content slides
    for slide in pitch.slides:
        s = prs.slides.add_slide(blank)
        page = slide.slide_number + 1

        # Citation markers and the numbered reference list for this slide: brochure pages behind
        # policy statements, web pages behind company statements, each numbered once per slide.
        refs: list[Reference] = []
        markers: dict[int, list[tuple[str, str | None]]] = {}

        def _number(cite: Reference) -> tuple[str, str | None]:
            if cite not in refs:
                refs.append(cite)
            return str(refs.index(cite) + 1), cite[1]

        for i, b in enumerate(slide.bullets):
            cites = [_cite(refs_by_chunk[cid]) for cid in b.source_chunk_ids[:2] if cid in refs_by_chunk]
            cites += [_cite_web(u) for u in b.source_urls[:2]]
            markers[i] = [_number(c) for c in dict.fromkeys(cites)]
        if not refs and slide.footnote:
            refs.append((slide.footnote, None))

        if slide.layout == "map":
            _rect(s, 0, 0, W, H, CANVAS)
            _header(s, slide.title, f"{page} / {total}")
            _footer(s, page, total, now.year, pitch.disclaimer)
            ref_top = _references(s, refs, FOOTER_Y - Inches(0.1), MARGIN, CONTENT_W)
            _draw_exposure_map(s, slide, markers, HEADER_H + Inches(0.18), ref_top - Inches(0.1))
            continue

        left, right, left_header, right_header = _plan_columns(slide)
        idx = {id(b): i for i, b in enumerate(slide.bullets)}
        to_items = lambda bs: [(b.text, markers.get(idx[id(b)], []), b.kind) for b in bs]
        measure = lambda bs: [t + ("  " + ",".join(n for n, _ in ms) if ms else "") for t, ms, _ in to_items(bs)]

        # Two layouts from the brief: open two-column (statements | assumptions or watch-outs), or a
        # single column framed in a white card on the light canvas.
        two_col = bool(right)
        if two_col:
            x0, cw = MARGIN, CONTENT_W
            ref_bottom = FOOTER_Y - Inches(0.12)
            top = HEADER_H + Inches(0.3)
        else:
            _rect(s, 0, 0, W, H, CANVAS)
            card_top, card_bottom = HEADER_H + Inches(0.3), FOOTER_Y - Inches(0.2)
            _card(s, Inches(0.4), card_top, W - Inches(0.8), card_bottom - card_top)
            x0, cw = Inches(0.75), W - Inches(1.5)
            ref_bottom = card_bottom - Inches(0.22)
            top = card_top + Inches(0.3)
        _header(s, slide.title, f"{page} / {total}")
        _footer(s, page, total, now.year, pitch.disclaimer)

        ref_top = _references(s, refs, ref_bottom, x0, cw)
        if slide.subtitle:
            _text(s, x0, top, cw, Inches(0.3), slide.subtitle, 13, SLATE)
            top += Inches(0.42)
        gap_above_refs = Inches(0.25) if refs else 0
        avail_in = Emu(ref_top - gap_above_refs - top).inches

        size = None
        if two_col:
            col_w = (cw - Inches(0.6)) / 2
            size = _fit(measure(left), measure(right), Emu(col_w).inches - 0.1, Emu(col_w).inches - 0.1, avail_in, header_in=0.6)
            if size is None:
                # Columns would not fit even at the smallest size: one column, assumptions last.
                left, right, left_header, right_header = left + right, [], None, None
        if not right:
            size = _fit(measure(left), [], Emu(cw).inches - 0.1, 0, avail_in, header_in=0) or BODY_SIZES[-1]

        if right:
            col_w = (cw - Inches(0.6)) / 2
            _body_column(s, x0, top, col_w, to_items(left), size, left_header)
            _body_column(s, x0 + col_w + Inches(0.6), top, col_w, to_items(right), size, right_header)
            divider_x = x0 + col_w + Inches(0.3)
            _line(s, divider_x, top - Inches(0.05), divider_x, min(ref_top - gap_above_refs, FOOTER_Y - Inches(0.45)), HAIRLINE, 0.75)
        else:
            _body_column(s, x0, top, cw, to_items(left), size, None)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return out_path


def audit_markdown(report: AuditReport, pitch: Pitch) -> str:
    sm = report.summary
    md = [f"# Audit report: {pitch.company_name} pitch v{pitch.version}", "", f"Evidence gate: **{sm.gate}** (generated {report.generated_at})", "",
          f"- Claims audited: {sm.total_claims} (material policy claims: {sm.material_claims})",
          f"- Supported: {sm.supported} | Partially supported: {sm.partially_supported} | Contradicted: {sm.contradicted} | Not found: {sm.not_found} | Uncertain: {sm.uncertain} | N/A: {sm.not_applicable}",
          f"- Evidence confidence: {sm.confidence_score:.0%} (share of material claims fully supported; decision-support metric)", ""]
    if report.major_policy_gaps:
        md += ["## Major policy gaps"] + [f"- {g}" for g in report.major_policy_gaps] + [""]
    md += ["## Claims", ""]
    for ca in report.claims:
        pp = ca.passport
        md.append(f"### [{ca.status.value}] Slide {ca.claim.slide}: {ca.claim.claim_text}")
        md.append(f"- Type: {ca.claim.claim_type.value} | material: {ca.claim.material} | action: {ca.action}")
        if pp.page:
            md.append(f"- Evidence: {pp.policy_name} p.{pp.page}" + (f", {pp.section}" if pp.section else "") + (f" ({pp.clause})" if pp.clause else ""))
            if pp.source_text:
                md.append(f"  > {pp.source_text[:300]}")
            if pp.retrieval_relevance is not None:
                md.append(f"- Retrieval relevance (not accuracy): {pp.retrieval_relevance}")
        for c in ca.checks:
            md.append(f"- {c.check}: {c.status.value} - {c.detail}")
        if ca.correction_hint:
            md.append(f"- Correction hint: {ca.correction_hint}")
        md.append("")
    md.append(f"_{report.note}_")
    return "\n".join(md)
