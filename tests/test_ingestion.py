"""Policy ingestion + chunking tests against the actual four brochures."""
from __future__ import annotations

from app.models.policy import ContentType

EXPECTED_IDS = {"hdfc_optima_secure_plus", "care_supreme", "abhi_activ_one", "niva_reassure_2"}


def test_registry_finds_exactly_four_policies(policies):
    ids = {doc.policy_id for _, doc in policies}
    assert ids == EXPECTED_IDS
    labels = {doc.short_label for _, doc in policies}
    assert labels == {"Policy A", "Policy B", "Policy C", "Policy D"}


def test_parser_pages_and_flags(parsed_docs):
    _, _, hdfc = parsed_docs["hdfc_optima_secure_plus"]
    _, _, niva = parsed_docs["niva_reassure_2"]
    assert hdfc.page_count == 16
    assert niva.page_count == 2
    assert niva.flags().get(1) == "image_only"  # cover page carries only the UIN
    hdfc_p11 = next(p for p in hdfc.pages if p.page_number == 11)
    assert hdfc_p11.mode == "table" and hdfc_p11.table_rows
    labels = [r[0] for r in hdfc_p11.table_rows]
    assert "Room Rent" in labels and "Emergency Ambulance" in labels


def test_chunks_have_required_metadata(chunked):
    for pid, chunks in chunked.items():
        assert len(chunks) > 20, pid
        for c in chunks:
            assert c.chunk_id.startswith(pid + ":")
            assert c.policy_id == pid and c.policy_name
            assert c.page_number >= 1
            assert c.clause and c.clause.startswith("p")
            assert c.source_text.strip()
            assert isinstance(c.content_type, ContentType)


def test_parent_child_structure(chunked):
    for pid, chunks in chunked.items():
        ids = {c.chunk_id for c in chunks}
        children = [c for c in chunks if c.parent_chunk_id]
        assert children, pid
        assert all(c.parent_chunk_id in ids for c in children), pid


def test_table_rows_render_label_value(chunked):
    care = chunked["care_supreme"]
    rows = [c for c in care if c.content_type == ContentType.TABLE_ROW]
    assert any(c.source_text.startswith("Room Rent:") for c in rows)
    ped = [c for c in care if c.content_type == ContentType.WAITING_PERIOD and "36 months" in c.source_text]
    assert ped and "Pre-Existing" in ped[0].source_text


def test_hdfc_exclusions_split_into_items(chunked):
    hdfc = chunked["hdfc_optima_secure_plus"]
    excl = [c for c in hdfc if c.content_type == ContentType.EXCLUSION and c.meta.get("list_item")]
    texts = {c.source_text.lower() for c in excl}
    assert "maternity" in texts
    assert "cosmetic surgery" in texts
    wp = [c for c in hdfc if c.content_type == ContentType.WAITING_PERIOD and c.meta.get("list_item")]
    assert any("36 months" in c.source_text and "pre-existing" in c.source_text.lower() for c in wp)


def test_hdfc_schedule_table_merged_cells(chunked):
    hdfc = chunked["hdfc_optima_secure_plus"]
    p11 = [c for c in hdfc if c.page_number == 11 and c.content_type == ContentType.TABLE_ROW]
    by_label = {c.source_text.split(":", 1)[0]: c.source_text for c in p11}
    assert by_label["AYUSH Treatment"].endswith("Up to sum insured")
    assert "INR 5,00,000" in by_label["Emergency Ambulance"]
    assert "INR 800 per day" in by_label["Daily Cash for Choosing Shared Accommodation"]


def test_footnotes_linked_to_clauses(chunked):
    niva = chunked["niva_reassure_2"]
    fn = {c.meta.get("footnote_marker"): c for c in niva if c.meta.get("footnote_marker")}
    assert "8" in fn and "48 hrs" in fn["8"].source_text
    hospital_cash = next(c for c in niva if c.source_text.startswith("Hospital Cash"))
    assert fn["8"].chunk_id in hospital_cash.footnote_refs

    abhi = chunked["abhi_activ_one"]
    fn = {c.meta.get("footnote_marker"): c for c in abhi if c.meta.get("footnote_marker")}
    assert "%" in fn and "Maternity" in fn["%"].source_text
    maternity = next(c for c in abhi if "Maternity Cover" in c.source_text and not c.meta.get("footnote_marker"))
    assert fn["%"].chunk_id in maternity.footnote_refs


def test_marketing_stats_are_typed_not_coverage(chunked):
    hdfc = chunked["hdfc_optima_secure_plus"]
    stat = [c for c in hdfc if c.source_text.startswith("98% health claims payout ratio")]
    assert stat and all(c.content_type == ContentType.MARKETING_STAT for c in stat)
    lives = [c for c in hdfc if "lives insured" in c.source_text]
    assert lives and all(c.content_type == ContentType.MARKETING_STAT for c in lives)


def test_percent_values_preserved(chunked):
    care = chunked["care_supreme"]
    cb = next(c for c in care if c.source_text.startswith("Cumulative Bonus:"))
    assert "50% of SI" in cb.source_text
