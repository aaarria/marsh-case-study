/** Backend graph nodes in the advisor's language. Keys match `app/graph/builder.py`. */
export const NODE_LABELS: Record<string, string> = {
  run: "Run",
  research_company: "Company profile",
  confirm_context: "Context check",
  map_exposures: "Exposures",
  compare_policies: "Policy comparison",
  policy_fit_arena: "Policy fit",
  confirm_recommendation: "Recommendation check",
  evidence_pack: "Evidence pack",
  generate_pitch: "Draft",
  audit_pitch: "Audit",
  human_review: "Your review",
  export_outputs: "Export",
  finalize_rejected: "Rejected",
};

/** What a step is doing while it runs (the wait is a teaching moment). */
export const NODE_EXPLAIN: Record<string, string> = {
  research_company: "Building the company profile. Your inputs are facts; web-sourced statements count as facts only when web research is on; everything else is a labelled assumption.",
  confirm_context: "Checking whether the deck would rest on assumptions alone.",
  map_exposures: "Turning company facts into the health-cover needs the pitch must address.",
  compare_policies: "Retrieving each brochure independently and extracting structured facts feature by feature.",
  policy_fit_arena: "Running the same client scenarios against every policy in scope and scoring fit deterministically.",
  confirm_recommendation: "Checking whether the top scores are close enough to need your call.",
  evidence_pack: "Assembling the only facts the writer may cite, each with its brochure page.",
  generate_pitch: "Drafting the slides. Every policy statement must cite an evidence id.",
  audit_pitch: "Five auditors test each claim: citation, numbers, exclusions, contradictions, coverage.",
  export_outputs: "Writing the editable PPTX deck and the claim-level audit report.",
  finalize_rejected: "Closing the run without a deck.",
};
