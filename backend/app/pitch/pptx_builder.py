"""Client deck in the current Marsh editorial language, plus the markdown audit report.

Brand reference: marsh.com. Midnight #000E47, warm ivory #F8F3EF, light blue #B6E8F4,
a sparing warm yellow. Georgia for headlines, Calibri for everything else. One idea per slide.
The cover is composed in type; there is no stock photograph. Policy lines stay tied to brochure
pages. Anything without a source is labelled, never dressed up as a fact.

Five slides: cover, client at a glance, exposure-to-benefit, perspective and watch-outs,
one recommendation. A deck produced before this layout still exports: each content slide falls
back to a short editorial list. Citations are plain source lines, not hyperlinks.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.models.pitch import AuditReport, Pitch, Slide, SlideBullet
from app.models.policy import SourceRef

MIDNIGHT = RGBColor(0x00, 0x0E, 0x47)
IVORY = RGBColor(0xF8, 0xF3, 0xEF)
BLUE = RGBColor(0xB6, 0xE8, 0xF4)
GOLD = RGBColor(0xC4, 0xA3, 0x5A)
INK = RGBColor(0x1C, 0x1A, 0x17)
MUTED = RGBColor(0x6B, 0x65, 0x60)
HAIR = RGBColor(0xE3, 0xDB, 0xD4)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

SERIF = "Georgia"
SANS = "Calibri"

W, H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.65)
LOGO_X = Inches(0.65)
LOGO_Y = Inches(0.35)
LOGO_W = Inches(1.18)

ASSETS = Path(__file__).parent / "assets"
LOGO_WHITE = ASSETS / "marsh-white.png"
LOGO_NAVY = ASSETS / "marsh-navy.png"
LOGO_LOCKUP = ASSETS / "marsh-mclennan.png"

# Small section labels, in the sample deck's voice. They do not change slide titles.
_SECTION = {
    "cover": ("01", "The advisory"),
    "glance": ("02", "The decision context"),
    "map": ("03", "The comparison"),
    "comparison": ("03", "The comparison"),
    "perspective": ("04", "The recommendation"),
    "why": ("04", "The recommendation"),
    "recommendation": ("05", "For the client"),
    "decision": ("05", "For the client"),
}

Reference = tuple[str, str | None]


def _prs() -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    return prs


def _emu(value) -> int:
    """PowerPoint rejects a coordinate written as a float."""
    return int(round(value))


def _rect(slide, x, y, w, h, fill: RGBColor):
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, _emu(x), _emu(y), _emu(w), _emu(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def _line(slide, x1, y1, x2, y2, color: RGBColor, width_pt: float = 0.75):
    ln = slide.shapes.add_connector(1, _emu(x1), _emu(y1), _emu(x2), _emu(y2))
    ln.line.color.rgb = color
    ln.line.width = Pt(width_pt)
    return ln


def _textbox(slide, x, y, w, h, *, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(_emu(x), _emu(y), _emu(w), _emu(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    return tb


def _run(p, text: str, size: float, color: RGBColor, *, bold=False, italic=False, font=SANS, superscript=False, link: str | None = None):
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.color.rgb = color
    r.font.bold = bold
    r.font.italic = italic
    r.font.name = font
    if superscript:
        r.font._element.set(qn("a:baseline"), "30000")
    if link:
        r.hyperlink.address = link
        r.font.underline = False
        r.font.color.rgb = color
    return r


def _text(slide, x, y, w, h, text: str, size: float, color: RGBColor = INK, *, bold=False, italic=False, align=PP_ALIGN.LEFT, font=SANS, anchor=MSO_ANCHOR.TOP):
    tb = _textbox(slide, x, y, w, h, anchor=anchor)
    p = tb.text_frame.paragraphs[0]
    p.alignment = align
    _run(p, text, size, color, bold=bold, italic=italic, font=font)
    return tb


def _logo(slide, path: Path, x, y, width):
    return slide.shapes.add_picture(str(path), _emu(x), _emu(y), width=_emu(width))


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return (cut or text[: limit - 1]).rstrip(".,;:") + "…"


def _parts(text: str) -> tuple[str, str]:
    raw = text.strip()
    for prefix in ("assumption:", "why this policy:", "watch-out:"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :].strip()
    if "|" in raw:
        label, body = raw.split("|", 1)
        return label.strip(), body.strip()
    if ":" in raw and len(raw.split(":", 1)[0]) <= 36:
        label, body = raw.split(":", 1)
        return label.strip(), body.strip()
    return "", raw


_GENERIC = {"TITLE", "EXPOSURE", "CARD", "BENEFIT", "ITEM", "POINT", "LABEL"}


def _named(label: str, body: str) -> tuple[str, str]:
    """A model often emits the placeholder word TITLE as the label. The real heading is the other half."""
    if not label or label.upper() in _GENERIC:
        return body, ""
    if body.strip().lower() == label.strip().lower():
        return label, ""
    return label, body


def _client_line(company: str, profile: list[tuple], subtitle: str | None) -> str:
    if subtitle and len(subtitle.split()) >= 8:
        return _short(subtitle, 220)
    by = {label.upper(): body for _, _, label, body in profile}

    def ok(value: str | None) -> str:
        if not value or value.strip().lower() in {"unknown", "not established", "none", "n/a"}:
            return ""
        return value.strip().rstrip(".")

    industry, scale, footprint = ok(by.get("INDUSTRY")), ok(by.get("SCALE")), ok(by.get("FOOTPRINT"))
    bits: list[str] = []
    if industry:
        word = industry[0].lower() + industry[1:]
        bits.append(word if word.startswith(("a ", "an ")) else f"a {word} business")
    if scale:
        bits.append(scale[0].lower() + scale[1:])
    if footprint:
        bits.append(footprint[0].lower() + footprint[1:])
    if not bits:
        return "Company facts are still thin. The points below are working assumptions for the medical programme."
    sentence = f"{company.strip() or 'This client'} is " + ", ".join(bits)
    return _short(sentence if sentence.endswith(".") else sentence + ".", 220)


def _status_label(kind: str, urls: list[str]) -> str:
    if kind == "assumption":
        return "ASSUMPTION"
    if kind == "company" and urls:
        return "VERIFIED"
    if kind == "company":
        return "INFERENCE"
    return "INFERENCE"


def _watch_tag(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("not explicitly", "not found", "does not address", "cannot be confirmed", "unknown")):
        return "GAP"
    if any(w in t for w in ("sub-limit", "sublimit", "capped", "limit")):
        return "LIMIT"
    if any(w in t for w in ("eligib", "condition", "subject to", "waiting")):
        return "CONDITION"
    return "WATCH"


def _cite(ref: SourceRef) -> Reference:
    text = f"{ref.policy_name or ref.policy_id}, p. {ref.page}"
    if ref.section:
        text += f", section \"{ref.section[:48]}\""
    return text, ""


def _cite_web(url: str) -> Reference:
    u = urlparse(url)
    host = u.netloc.removeprefix("www.")
    path = unquote(u.path).strip("/").replace("_", " ").replace("-", " ")
    if len(path) > 60:
        path = path[:57] + "…"
    return f"{host}" + (f" · {path}" if path else ""), url


def _provenance(ref: SourceRef | None) -> str:
    if ref is None:
        return "Not stated as a confirmed exclusion"
    text = f"{ref.policy_name or ref.policy_id}, p. {ref.page}"
    if ref.section:
        section = " ".join(ref.section.split())
        section = re.split(r"\s+AS IT\b", section, maxsplit=1, flags=re.I)[0].strip(" ,;:-")
        if len(section) > 52:
            section = section[:52].rsplit(" ", 1)[0].rstrip(" ,;:-")
        text += f', section "{section}"'
    return text


def _markers_for(slide: Slide, refs_by_chunk: dict[str, SourceRef]) -> tuple[dict[int, list[Reference]], list[Reference]]:
    refs: list[Reference] = []
    markers: dict[int, list[Reference]] = {}

    def _number(cite: Reference) -> Reference:
        if cite not in refs:
            refs.append(cite)
        return cite

    for i, b in enumerate(slide.bullets):
        cites = [_cite(refs_by_chunk[cid]) for cid in b.source_chunk_ids[:1] if cid in refs_by_chunk]
        cites += [_cite_web(u) for u in b.source_urls[:1]]
        markers[i] = [_number(c) for c in dict.fromkeys(cites)]
    return markers, refs


def _footer(slide, page: int, year: int):
    """Content-slide footer from the sample. Text and a page number. The logo stays in the header."""
    del year
    _line(slide, Inches(0.62), Inches(7.12), Inches(12.71), Inches(7.12), HAIR, 0.75)
    _text(slide, Inches(0.62), Inches(7.18), Inches(8.2), Inches(0.24), "Brochure evidence. Policy wording prevails.", 10, MUTED, anchor=MSO_ANCHOR.MIDDLE)
    _text(slide, Inches(11.60), Inches(7.18), Inches(1.10), Inches(0.24), f"{page:02d}", 11, MIDNIGHT, align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)


def _source_lines(slide, refs: list[Reference], y):
    """Quiet citation text. The line is not a hyperlink."""
    if not refs:
        return
    tb = _textbox(slide, MARGIN, y, W - 2 * MARGIN, Inches(0.42))
    tf = tb.text_frame
    for k, (text, _link) in enumerate(refs[:4]):
        p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
        p.space_after = Pt(1)
        _run(p, f"Source: {text}", 10, MUTED)


def _mark(p, cites: list[Reference], size: float):
    for n, (_text, _link) in enumerate(cites, start=1):
        _run(p, (" " if n == 1 else ",") + str(n), size, MIDNIGHT, superscript=True)


def _blank(prs) -> object:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _rect(s, 0, 0, W, H, IVORY)
    return s


def _rail(slide):
    """The sample's navy rail. Same thickness on every slide."""
    _rect(slide, 0, 0, Inches(0.12), H, MIDNIGHT)


def _one_logo(slide, *, cover: bool):
    """One Marsh McLennan lockup. Cover sits top left; every other slide sits top right, as in the sample."""
    if cover:
        _logo(slide, LOGO_LOCKUP, Inches(0.62), Inches(0.48), Inches(2.50))
    else:
        _logo(slide, LOGO_LOCKUP, Inches(11.35), Inches(0.28), Inches(1.40))


def _section(slide, number: str, label: str, *, y=0.32):
    _text(slide, Inches(0.62), Inches(y), Inches(0.55), Inches(0.24), number, 12, GOLD, bold=True)
    _text(slide, Inches(1.25), Inches(y), Inches(8.0), Inches(0.24), label, 12, MUTED)


def _cover(slide, content: Slide, pitch: Pitch):
    """Cover in the sample's order: logo, heading, subtitle, company, preparation line, date in the footer band."""
    name = (content.bullets[0].text if content.bullets else pitch.company_name).strip() or "the client"
    when = content.bullets[1].text.strip() if len(content.bullets) > 1 else ""
    kicker = content.bullets[2].text.strip() if len(content.bullets) > 2 else "Prepared by Marsh McLennan"
    _section(slide, *_SECTION["cover"], y=1.35)
    _text(slide, Inches(0.62), Inches(1.75), Inches(11.2), Inches(0.62), content.title or "Health Policy Advisory", 36, MIDNIGHT, font=SERIF)
    _text(slide, Inches(0.62), Inches(2.50), Inches(10.5), Inches(0.50), content.subtitle or "Evidence-led health insurance recommendation", 20, MIDNIGHT, font=SERIF)
    _text(slide, Inches(0.62), Inches(3.20), Inches(11.2), Inches(0.85), name, 36, MIDNIGHT, font=SERIF)
    _rect(slide, Inches(0.62), Inches(4.20), Inches(1.35), Inches(0.035), GOLD)
    _text(slide, Inches(0.62), Inches(4.40), Inches(10), Inches(0.36), kicker, 16, MUTED)
    _rect(slide, 0, Inches(6.55), W, Inches(0.95), MIDNIGHT)
    if when:
        _text(slide, Inches(8.2), Inches(6.82), Inches(4.5), Inches(0.36), when, 14, WHITE, align=PP_ALIGN.RIGHT)


def _brand(slide):
    """Content slides share the cover chrome. The logo is not drawn here."""
    del slide


def _cols(text: str) -> list[str]:
    return [part.strip() for part in text.split("|")]


def _kicker(slide, page: int, label: str):
    """Section label is drawn once with the logo. Layouts must not add a second one."""
    del slide, page, label


def _slide_title(slide, text: str):
    _text(slide, Inches(0.62), Inches(0.64), Inches(10.4), Inches(0.58), text, 32, MIDNIGHT, bold=True, font=SERIF)


def _glance(slide, content: Slide, markers: dict[int, list[Reference]], page: int):
    labels = {_parts(b.text)[0].upper() for b in content.bullets}
    if {"PRIORITY", "LENS", "CONSIDERED", "WHY", "ASSESSED"} & labels:
        _priorities(slide, content, page)
        return
    _kicker(slide, page, "The client")
    _text(slide, MARGIN, Inches(0.88), Inches(10.2), Inches(0.5), _short(content.title or "Decision context", 64), 28, MIDNIGHT, bold=True, font=SERIF)
    named = {"INDUSTRY", "SCALE", "FOOTPRINT"}
    profile, cards = [], []
    for i, b in enumerate(content.bullets):
        if b.kind not in {"company", "assumption"}:
            continue
        label, body = _named(*_parts(b.text))
        if label.upper() in named and len(profile) < 3:
            profile.append((i, b, label, body))
        elif len(cards) < 4:
            cards.append((i, b, label, body))
    if len(profile) < 3:
        for i, b in enumerate(content.bullets):
            if b.kind not in {"company", "assumption"}:
                continue
            label, body = _named(*_parts(b.text))
            if any(i == item[0] for item in profile) or any(i == item[0] for item in cards):
                continue
            if len(profile) < 3:
                profile.append((i, b, label, body))

    company = re.sub(r"(?i)\s+at a glance$", "", content.title or "").strip()
    _text(slide, MARGIN, Inches(1.55), Inches(6.4), Inches(2.4), _client_line(company, profile, content.subtitle), 18, INK, font=SERIF)

    labels = ["INDUSTRY", "SCALE", "FOOTPRINT"]
    for n, item in enumerate(profile[:3]):
        i, b, label, body = item
        y = Inches(1.6) + n * Inches(1.15)
        _text(slide, Inches(7.5), y, Inches(5.1), Inches(0.28), (label or labels[n]).upper(), 11, MUTED, bold=True)
        tb = _textbox(slide, Inches(7.5), y + Inches(0.26), Inches(5.1), Inches(0.36))
        p = tb.text_frame.paragraphs[0]
        _run(p, _short(body or b.text, 90), 16, INK)
        _mark(p, markers.get(i, []), 16)
        if b.kind == "assumption" and (body or "").strip().lower() != "not established":
            _text(slide, Inches(7.5), y + Inches(0.64), Inches(5.1), Inches(0.2), "Not established", 10, MUTED)

    if not cards:
        return
    _line(slide, MARGIN, Inches(5.05), W - MARGIN, Inches(5.05), HAIR)
    width = (W - 2 * MARGIN - Inches(0.28) * (len(cards[:4]) - 1)) / max(len(cards[:4]), 1)
    for n, (i, b, label, body) in enumerate(cards[:4]):
        x = MARGIN + n * (width + Inches(0.28))
        heading, detail = _named(label, body)
        _rect(slide, x, Inches(5.25), width, Inches(0.08), BLUE)
        _text(slide, x, Inches(5.42), width, Inches(0.55), _short(heading or "Exposure", 48), 14, MIDNIGHT, font=SERIF)
        if detail:
            tb = _textbox(slide, x, Inches(6.0), width, Inches(0.7))
            p = tb.text_frame.paragraphs[0]
            _run(p, _short(detail, 90), 12, INK)
            _mark(p, markers.get(i, []), 12)


def _journey(slide, content: Slide, markers: dict[int, list[Reference]], refs_by_chunk: dict[str, SourceRef], page: int):
    _kicker(slide, page, "The fit")
    _text(slide, MARGIN, Inches(0.7), Inches(12), Inches(0.46), _short(content.title or "From exposure to benefit", 48), 28, MIDNIGHT, font=SERIF)
    if content.subtitle:
        _text(slide, MARGIN, Inches(1.16), Inches(12), Inches(0.28), _short(content.subtitle, 140), 13, MUTED)
    exposures = [(i, b) for i, b in enumerate(content.bullets) if b.kind in {"company", "assumption"}]
    benefits = [(i, b) for i, b in enumerate(content.bullets) if b.kind == "policy"]
    why = next((b for b in content.bullets if b.kind == "recommendation"), None)
    rows = min(3, max(len(exposures), len(benefits), 1))
    heads = ["EXPOSURE", "CLIENT NEED", "POLICY BENEFIT", "EVIDENCE"]
    xs = [MARGIN, Inches(3.7), Inches(6.85), Inches(10.0)]
    ws = [Inches(2.7), Inches(2.85), Inches(2.85), Inches(2.7)]
    for x, w, head in zip(xs, ws, heads):
        _text(slide, x, Inches(1.55), w, Inches(0.24), head, 11, MUTED, bold=True)
    for r in range(rows):
        y = Inches(1.95) + r * Inches(1.15)
        _line(slide, MARGIN, y - Inches(0.08), W - MARGIN, y - Inches(0.08), HAIR)
        exp = exposures[r] if r < len(exposures) else None
        ben = benefits[r] if r < len(benefits) else None
        if exp:
            label, body = _named(*_parts(exp[1].text))
            _text(slide, xs[0], y, ws[0], Inches(0.85), _short(label or body, 42), 16, MIDNIGHT, font=SERIF)
            _text(slide, xs[1], y, ws[1], Inches(0.9), _short(body if label else "", 80) or "Identified for this client", 14, INK)
        if ben:
            label, body = _named(*_parts(ben[1].text))
            tb = _textbox(slide, xs[2], y, ws[2], Inches(0.9))
            p = tb.text_frame.paragraphs[0]
            _run(p, _short(label or body, 70), 15, INK)
            _mark(p, markers.get(ben[0], []), 15)
            ref = next((refs_by_chunk[c] for c in ben[1].source_chunk_ids if c in refs_by_chunk), None)
            cites = markers.get(ben[0]) or []
            tb = _textbox(slide, xs[3], y, ws[3], Inches(0.9))
            p = tb.text_frame.paragraphs[0]
            _run(p, _short(f"Source: {_provenance(ref)}" if ref else "", 70), 13, MUTED)
        if exp:
            _text(slide, Inches(3.32), y + Inches(0.12), Inches(0.32), Inches(0.3), "→", 14, MIDNIGHT)
        if ben:
            _text(slide, Inches(6.48), y + Inches(0.12), Inches(0.32), Inches(0.3), "→", 14, MIDNIGHT)
            _text(slide, Inches(9.62), y + Inches(0.12), Inches(0.32), Inches(0.3), "→", 14, MIDNIGHT)
    bar_y = Inches(5.35)
    _rect(slide, MARGIN, bar_y, W - 2 * MARGIN, Inches(0.9), MIDNIGHT)
    _rect(slide, MARGIN, bar_y, Inches(0.08), Inches(0.95), GOLD)
    _text(slide, MARGIN + Inches(0.28), bar_y + Inches(0.1), Inches(11.5), Inches(0.24), "WHY THIS POLICY FITS", 11, GOLD, bold=True)
    sentence = _parts(why.text)[1] if why else "Selected on the evidence in this deck. Advisor review is still required."
    sentence = re.sub(r"(?i)\bbest\b", "closer", sentence)
    _text(slide, MARGIN + Inches(0.28), bar_y + Inches(0.36), Inches(11.6), Inches(0.48), _short(sentence, 160), 16, WHITE)


def _perspective(slide, content: Slide, markers: dict[int, list[Reference]], refs_by_chunk: dict[str, SourceRef], page: int):
    _kicker(slide, page, "Perspective")
    _text(slide, MARGIN, Inches(0.7), Inches(6), Inches(0.42), _short(content.title or "Why Marsh", 36), 26, MIDNIGHT, font=SERIF)
    _text(slide, Inches(7.15), Inches(0.7), Inches(5.5), Inches(0.42), "What to watch", 26, MIDNIGHT, font=SERIF)
    if content.subtitle:
        _text(slide, MARGIN, Inches(1.14), Inches(12), Inches(0.26), _short(content.subtitle, 140), 13, MUTED)
    _line(slide, Inches(6.85), Inches(1.55), Inches(6.85), Inches(6.35), HAIR)
    marsh = [(i, b) for i, b in enumerate(content.bullets) if b.kind == "marsh"][:3]
    for n, (i, b) in enumerate(marsh):
        label, body = _parts(b.text)
        y = Inches(1.7) + n * Inches(1.4)
        _text(slide, MARGIN, y, Inches(5.8), Inches(0.3), (label or "Perspective").upper(), 12, MIDNIGHT, bold=True)
        _text(slide, MARGIN, y + Inches(0.32), Inches(5.8), Inches(0.7), _short(body or b.text, 110), 16, INK)
    watches = [(i, b) for i, b in enumerate(content.bullets) if b.kind != "marsh"][:4]
    for n, (i, b) in enumerate(watches):
        label, body = _parts(b.text)
        tag = label.upper() if label.upper() in {"WATCH", "LIMIT", "GAP", "CONDITION"} else _watch_tag(b.text)
        y = Inches(1.65) + n * Inches(1.2)
        _rect(slide, Inches(7.15), y, Inches(1.35), Inches(0.28), BLUE)
        _text(slide, Inches(7.15), y, Inches(1.35), Inches(0.28), tag, 11, MIDNIGHT, bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        ref = next((refs_by_chunk[c] for c in b.source_chunk_ids if c in refs_by_chunk), None)
        note = _provenance(ref) if ref else ("Not confirmed in the brochure" if tag == "GAP" else "")
        tb = _textbox(slide, Inches(8.65), y, Inches(3.95), Inches(1.0))
        p = tb.text_frame.paragraphs[0]
        _run(p, _short(body or b.text, 130), 14, INK)
        _mark(p, markers.get(i, []), 14)
        if note:
            p2 = tb.text_frame.add_paragraph()
            p2.space_before = Pt(3)
            cites = markers.get(i) or []
            _run(p2, _short(f"Source: {note}" if note and not note.startswith("Source:") else note, 72), 11, MUTED)


def _recommendation(slide, content: Slide, pitch: Pitch, markers: dict[int, list[Reference]], refs_by_chunk: dict[str, SourceRef], page: int):
    _kicker(slide, page, "The decision")
    _text(slide, MARGIN, Inches(0.68), Inches(8), Inches(0.6), _short(content.title or "One policy. Clear rationale.", 42), 28, MIDNIGHT, font=SERIF)
    name = content.subtitle or content.title
    name = re.sub(r"(?i)^recommended policy:\s*", "", name or "").strip() or pitch.recommended_policy_id
    _text(slide, MARGIN, Inches(1.4), Inches(8.2), Inches(0.85), _short(name, 48), 32, MIDNIGHT, font=SERIF)

    score = None
    reasons = []
    trade = None
    for i, b in enumerate(content.bullets):
        label, body = _parts(b.text)
        if label.upper() == "SCORE" or re.search(r"\d+(?:\.\d+)?\s*/\s*100", b.text):
            found = re.search(r"(\d+(?:\.\d+)?)\s*/\s*100", body or b.text)
            if found:
                score = found.group(1)
            continue
        if label.upper() == "TRADEOFF" or b.kind == "assumption":
            trade = trade or _short(body or b.text, 140)
            continue
        if b.kind in {"recommendation", "policy"} and len(reasons) < 3:
            reasons.append((i, b, body or b.text))
    for n, (i, b, text) in enumerate(reasons):
        y = Inches(2.45) + n * Inches(1.05)
        _rect(slide, MARGIN, y, Inches(0.06), Inches(0.7), BLUE)
        tb = _textbox(slide, Inches(1.4), y, Inches(7.2), Inches(0.8))
        p = tb.text_frame.paragraphs[0]
        _run(p, _short(text, 110), 16, INK)
        _mark(p, markers.get(i, []), 16)
        ref = next((refs_by_chunk[c] for c in b.source_chunk_ids if c in refs_by_chunk), None)
        if ref:
            _text(slide, Inches(1.4), y + Inches(0.42), Inches(7.2), Inches(0.28), _short(_provenance(ref), 70), 11, MUTED)

    del score
    _rect(slide, Inches(9.35), Inches(1.45), Inches(3.35), Inches(2.15), MIDNIGHT)
    _text(slide, Inches(9.55), Inches(1.65), Inches(2.95), Inches(0.28), "FOR THE CLIENT", 11, GOLD, bold=True)
    _text(slide, Inches(9.55), Inches(2.05), Inches(2.95), Inches(1.3), "Review the evidence before this recommendation is presented.", 14, WHITE)

    if trade:
        _text(slide, MARGIN, Inches(5.7), Inches(3), Inches(0.26), "KEY TRADE-OFF", 11, MUTED, bold=True)
        _text(slide, MARGIN, Inches(6.0), Inches(8.2), Inches(0.55), trade, 15, INK)
    _text(slide, Inches(9.35), Inches(6.15), Inches(3.35), Inches(0.35), "Advisor review required", 13, MIDNIGHT, align=PP_ALIGN.RIGHT)


def _priorities(slide, content: Slide, page: int):
    """Sample geometry: context on the left, profile facts on the right, assessed needs along the bottom."""
    del page
    _slide_title(slide, content.title or "The decision context")
    priorities = []
    for bullet in content.bullets:
        label, body = _parts(bullet.text)
        if label.upper() == "PRIORITY" and body:
            heading, _, detail = body.partition("|")
            priorities.append((heading.strip(), detail.strip()))
    weight = next((body for label, body in (_parts(b.text) for b in content.bullets) if label.upper() == "WEIGHT" and body), "")
    why = next((body for label, body in (_parts(b.text) for b in content.bullets) if label.upper() == "WHY" and body), "")
    assessed = []
    for label, body in (_parts(b.text) for b in content.bullets):
        if label.upper() == "ASSESSED" and body:
            assessed.extend(part.strip() for part in body.split("|") if part.strip())
        elif label.upper() == "CONSIDERED" and body:
            assessed.append(body)
    lens = next((body for label, body in (_parts(b.text) for b in content.bullets) if label.upper() == "LENS" and body), "")
    facts = [(label, body) for label, body in (_parts(b.text) for b in content.bullets) if label.upper() in {"INDUSTRY", "SCALE", "FOOTPRINT", "EMPLOYEES"} and body and body.lower() != "not established"]

    y = Inches(1.48)
    if priorities:
        _text(slide, Inches(0.62), y, Inches(6.4), Inches(0.20), "CLIENT PRIORITY", 11, MUTED, bold=True)
        _text(slide, Inches(0.62), y + Inches(0.22), Inches(6.4), Inches(0.55), priorities[0][0], 18, MIDNIGHT, font=SERIF)
        y += Inches(0.82)
        if weight:
            _text(slide, Inches(0.62), y, Inches(6.4), Inches(0.28), f"Priority weight {weight}", 14, INK)
            y += Inches(0.32)
    if why:
        room = Inches(4.85) - y
        if room > Inches(0.4):
            _text(slide, Inches(0.62), y, Inches(6.4), room, why, 15, INK, font=SERIF)

    fy = Inches(1.55)
    for label, body in facts[:3]:
        _text(slide, Inches(7.50), fy, Inches(5.1), Inches(0.22), label.upper(), 11, MUTED, bold=True)
        _text(slide, Inches(7.50), fy + Inches(0.24), Inches(5.1), Inches(0.70), body, 15, INK)
        fy += Inches(1.05)
    if not facts and lens:
        _text(slide, Inches(7.50), Inches(1.55), Inches(5.1), Inches(0.22), "DECISION LENS", 11, MUTED, bold=True)
        _text(slide, Inches(7.50), Inches(1.82), Inches(5.1), Inches(2.6), lens, 15, INK)
        lens = ""

    _line(slide, Inches(0.62), Inches(4.95), Inches(12.71), Inches(4.95), HAIR, 0.75)
    if lens:
        _text(slide, Inches(0.62), Inches(5.08), Inches(12.0), Inches(0.20), "DECISION LENS", 11, MUTED, bold=True)
        _text(slide, Inches(0.62), Inches(5.28), Inches(12.0), Inches(0.55), lens, 14, INK)
    if assessed:
        _text(slide, Inches(0.62), Inches(5.90), Inches(12.0), Inches(0.20), "WHAT WAS ASSESSED", 11, MUTED, bold=True)
        _text(slide, Inches(0.62), Inches(6.12), Inches(12.0), Inches(0.85), "   ·   ".join(assessed[:7]), 14, MIDNIGHT)


def _comparison(slide, content: Slide, markers: dict[int, list[Reference]], refs_by_chunk: dict[str, SourceRef], page: int):
    """A full-width comparison. The recommended column is shaded. Cells stay short."""
    del markers, refs_by_chunk, page
    _slide_title(slide, content.title or "How the policies compare")
    header = next((_cols(b.text)[1:] for b in content.bullets if _cols(b.text)[:1] == ["COLUMNS"]), [])
    recommended = next((_parts(b.text)[1] for b in content.bullets if _parts(b.text)[0].upper() == "REC"), "")
    rows = [(_cols(b.text)[1:], b) for b in content.bullets if _cols(b.text)[:1] == ["ROW"]][:8]
    if not header and rows:
        header = ["Recommended policy"]
    ncol = max(len(header), max((len(r[0]) - 1 for r in rows), default=0), 1)
    header = (header + [""] * ncol)[:ncol]
    highlight = header.index(recommended) if recommended in header else 0
    label_w = Inches(2.35)
    rest = W - MARGIN - label_w - Inches(0.15)
    col_w = rest / ncol
    x0 = MARGIN + label_w
    top = Inches(1.32)
    head_h = Inches(0.62)
    body_bottom = Inches(6.98)
    row_h = (body_bottom - top - head_h) / max(len(rows), 1)
    _text(slide, Inches(0.42), top + Inches(0.08), label_w - Inches(0.08), Inches(0.40), "DECISION FACTOR", 11, MUTED, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    for n, policy_name in enumerate(header):
        x = x0 + n * col_w + Inches(0.04)
        if n == highlight:
            _text(slide, x, top, col_w - Inches(0.10), Inches(0.16), "RECOMMENDED", 10, MIDNIGHT, bold=True)
        _text(slide, x, top + Inches(0.16), col_w - Inches(0.10), Inches(0.42), policy_name, 12, MIDNIGHT, bold=True, font=SERIF, anchor=MSO_ANCHOR.MIDDLE)
    for n, (cols, _bullet) in enumerate(rows):
        y = top + head_h + n * row_h
        _line(slide, Inches(0.42), y, x0 + ncol * col_w - Inches(0.04), y, HAIR, 0.6)
        _text(slide, Inches(0.42), y + Inches(0.04), label_w - Inches(0.08), row_h - Inches(0.06), cols[0] if cols else "", 14, MIDNIGHT, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        for c in range(ncol):
            phrase = cols[c + 1] if c + 1 < len(cols) else ""
            x = x0 + c * col_w + Inches(0.04)
            _text(slide, x, y + Inches(0.03), col_w - Inches(0.10), row_h - Inches(0.06), phrase, 14, INK, anchor=MSO_ANCHOR.MIDDLE)


def _reason_grid(slide, points, refs_by_chunk: dict[str, SourceRef], top):
    """Criterion, explanation, source. Two columns when there are several reasons. No numbered markers."""
    if not points:
        return
    cols = 2 if len(points) > 1 else 1
    rows = (len(points) + cols - 1) // cols
    width = Inches(5.95)
    gap = Inches(0.22)
    avail = Inches(7.00) - top
    step = avail / max(rows, 1)
    for n, (b, (label, body)) in enumerate(points):
        col = n % cols
        row = n // cols
        x = Inches(0.62) + col * (width + gap)
        y = top + row * step
        _text(slide, x, y, width, Inches(0.26), label, 14, MIDNIGHT, bold=True)
        _text(slide, x, y + Inches(0.28), width, step - Inches(0.52), body, 14, INK)
        ref = next((refs_by_chunk[c] for c in b.source_chunk_ids if c in refs_by_chunk), None)
        if ref:
            _text(slide, x, y + step - Inches(0.22), width, Inches(0.20), f"Source: {_provenance(ref)}", 9, MUTED)


def _policy_line(slide, name: str):
    if name:
        _text(slide, Inches(0.62), Inches(1.26), Inches(11.5), Inches(0.26), name, 14, MUTED)


def _why(slide, content: Slide, markers: dict[int, list[Reference]], refs_by_chunk: dict[str, SourceRef], page: int):
    """The recommended policy, then the generated reasons. Spacing follows the sample."""
    del markers, page
    _slide_title(slide, content.title or "Why this policy fits")
    policy = next((_parts(b.text)[1] for b in content.bullets if _parts(b.text)[0].upper() == "POLICY"), "")
    _policy_line(slide, policy or (content.subtitle or ""))
    points = [(b, _parts(b.text)) for b in content.bullets if _parts(b.text)[0].upper() not in {"POLICY", "COLUMNS", "ALTERNATIVE", "REC"} and not b.text.startswith("Alternative|")][:6]
    _reason_grid(slide, points, refs_by_chunk, Inches(1.60))


def _decision(slide, content: Slide, markers: dict[int, list[Reference]], refs_by_chunk: dict[str, SourceRef], page: int):
    """Client takeaway from the generated blocks. An empty alternative is not given a section."""
    del markers, page
    _slide_title(slide, content.title or "What this means for the client")
    policy = next((_parts(b.text)[1] for b in content.bullets if _parts(b.text)[0].upper() == "POLICY"), "")
    _policy_line(slide, policy)
    points = [(b, _parts(b.text)) for b in content.bullets if _parts(b.text)[0].upper() not in {"POLICY", "ALTERNATIVE", "COLUMNS", "REC"} and not b.text.startswith("Alternative|")][:6]
    _reason_grid(slide, points, refs_by_chunk, Inches(1.60))


def _profile_slide(content: Slide) -> bool:
    """A client-profile slide keeps the glance layout even if an edit renames the layout."""
    labels = set()
    for bullet in content.bullets:
        label, _body = _parts(bullet.text)
        labels.add(label.upper())
    return {"INDUSTRY", "FOOTPRINT"} <= labels and bool(labels & {"SCALE", "EMPLOYEES"})


def _editorial_list(slide, content: Slide, page: int, markers: dict[int, list[Reference]]):
    """Fallback for a deck whose slides are not in the five-part layout. Still editorial, still cited.

    Prints the same title, subtitle, and bullet text the canvas fallback shows.
    """
    _kicker(slide, page, "Advisory")
    _text(slide, MARGIN, Inches(0.88), Inches(12), Inches(0.5), _short(content.title or "Notes", 70), 32, MIDNIGHT, bold=True, font=SERIF)
    y = Inches(1.4)
    if content.subtitle:
        _text(slide, MARGIN, y, W - 2 * MARGIN, Inches(0.4), _short(content.subtitle, 160), 16, MUTED)
        y = Inches(1.9)
    for i, b in enumerate(content.bullets[:5]):
        tb = _textbox(slide, MARGIN, y, W - 2 * MARGIN, Inches(0.7))
        p = tb.text_frame.paragraphs[0]
        _run(p, _short(b.text.strip(), 180), 16, INK)
        _mark(p, markers.get(i, []), 16)
        y += Inches(0.75)


def build_pitch_deck(pitch: Pitch, refs_by_chunk: dict[str, SourceRef], out_path: Path, audit: AuditReport | None = None) -> Path:
    prs = _prs()
    now = datetime.now(timezone.utc)
    for n, content in enumerate(pitch.slides, start=1):
        s = _blank(prs)
        markers, refs = _markers_for(content, refs_by_chunk)
        _rail(s)
        if content.layout == "cover":
            _one_logo(s, cover=True)
            _cover(s, content, pitch)
            continue
        _one_logo(s, cover=False)
        number, label = _SECTION.get(content.layout, (f"{n:02d}", "Advisory"))
        _section(s, number, label)
        _brand(s)
        if content.layout == "glance" or _profile_slide(content):
            _glance(s, content, markers, n)
        elif content.layout == "map":
            _journey(s, content, markers, refs_by_chunk, n)
        elif content.layout == "perspective":
            _perspective(s, content, markers, refs_by_chunk, n)
        elif content.layout == "comparison":
            _comparison(s, content, markers, refs_by_chunk, n)
        elif content.layout == "why":
            _why(s, content, markers, refs_by_chunk, n)
        elif content.layout == "decision":
            _decision(s, content, markers, refs_by_chunk, n)
        elif content.layout == "recommendation":
            _recommendation(s, content, pitch, markers, refs_by_chunk, n)
        else:
            _editorial_list(s, content, n, markers)
        if refs and content.layout not in {"glance", "map", "perspective", "recommendation", "comparison", "why", "decision"}:
            _source_lines(s, refs, Inches(6.62))
        _footer(s, n, now.year)
    if audit is not None:
        pass  # the audit travels in the companion report, not as a sixth slide
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
