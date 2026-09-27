"""Gold-set page recall against the four actual brochures. Retrieval params are not tuned here."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

GOLD_PATH = Path(__file__).resolve().parent / "fixtures" / "retrieval_gold.json"
EXPECTED_POLICIES = {"abhi_activ_one", "care_supreme", "hdfc_optima_secure_plus", "niva_reassure_2"}


def _gold() -> list[dict]:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def test_gold_set_covers_four_policies_and_topics():
    gold = _gold()
    ids = {item["policy_id"] for item in gold}
    assert ids == EXPECTED_POLICIES
    blob = " ".join(item["query"].lower() for item in gold)
    for topic in (
        "hospital",
        "room rent",
        "icu",
        "pre-hospital",
        "post-hospital",
        "waiting",
        "pre-existing",
        "maternity",
        "accident",
        "ambulance",
        "recharge",
        "restore",
        "booster",
        "non-medical",
        "deductible",
        "co-payment",
        "exclusion",
        "add-on",
        "wellness",
        "domiciliary",
        "global",
    ):
        assert topic in blob, topic
    for item in gold:
        assert item["expected_page"] >= 1
        assert item["expected_phrase"]


def test_gold_page_recall_top3_and_top8(retriever):
    gold = _gold()
    hits = {1: 0, 3: 0, 5: 0, 8: 0}
    per = defaultdict(lambda: {1: [0, 0], 3: [0, 0], 5: [0, 0], 8: [0, 0]})
    failed = []
    for item in gold:
        ev = retriever.retrieve_for_policy(item["query"], item["policy_id"], top_k=8, use_cache=False, rerank=False)
        pages = [r.chunk.page_number for r in ev.results]
        pid = item["policy_id"]
        for k in (1, 3, 5, 8):
            per[pid][k][1] += 1
            ok = item["expected_page"] in pages[:k]
            if ok:
                hits[k] += 1
                per[pid][k][0] += 1
            elif k == 8:
                failed.append((pid, item["query"], item["expected_page"], pages))
    n = len(gold)
    assert not failed, failed
    assert hits[3] / n == 1.0
    assert hits[5] / n == 1.0
    assert hits[8] / n == 1.0
    for pid in EXPECTED_POLICIES:
        a, b = per[pid][8]
        assert a == b and b > 0, pid
    # top-1 is reported, not a gate: schedule tables can outrank narrative pages
    assert hits[1] / n >= 0.75
