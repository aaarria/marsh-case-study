"""LangGraph StateGraph for the advisory workflow with SQLite checkpointing and advisor questions (interrupts)."""
from __future__ import annotations

import sqlite3
import threading

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graph import nodes
from app.graph.state import AdvisoryState

_graph = None
_lock = threading.Lock()


def build_graph(checkpointer=None):
    g = StateGraph(AdvisoryState)
    g.add_node("research_company", nodes.research_node)
    g.add_node("confirm_context", nodes.context_node)
    g.add_node("map_exposures", nodes.exposures_node)
    g.add_node("compare_policies", nodes.compare_node)
    g.add_node("policy_fit_arena", nodes.arena_node)
    g.add_node("confirm_recommendation", nodes.close_call_node)
    g.add_node("evidence_pack", nodes.evidence_pack_node)
    g.add_node("generate_pitch", nodes.pitch_node)
    g.add_node("audit_pitch", nodes.audit_node)
    g.add_node("human_review", nodes.human_review_node)
    g.add_node("export_outputs", nodes.export_node)
    g.add_node("finalize_rejected", nodes.reject_node)

    g.add_edge(START, "research_company")
    g.add_edge("research_company", "confirm_context")
    g.add_conditional_edges("confirm_context", nodes.route_after_context, {"research_company": "research_company", "map_exposures": "map_exposures"})
    g.add_edge("map_exposures", "compare_policies")
    g.add_edge("compare_policies", "policy_fit_arena")
    g.add_edge("policy_fit_arena", "confirm_recommendation")
    g.add_edge("confirm_recommendation", "evidence_pack")
    g.add_edge("evidence_pack", "generate_pitch")
    g.add_edge("generate_pitch", "audit_pitch")
    g.add_edge("audit_pitch", "human_review")
    g.add_conditional_edges(
        "human_review",
        nodes.route_after_review,
        {"export_outputs": "export_outputs", "audit_pitch": "audit_pitch", "generate_pitch": "generate_pitch", "finalize_rejected": "finalize_rejected", "human_review": "human_review"},
    )
    g.add_edge("export_outputs", END)
    g.add_edge("finalize_rejected", END)
    return g.compile(checkpointer=checkpointer)


def get_graph():
    global _graph
    with _lock:
        if _graph is None:
            settings = get_settings()
            path = settings.storage_path / "sqlite" / "checkpoints.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            _graph = build_graph(SqliteSaver(conn))
        return _graph
