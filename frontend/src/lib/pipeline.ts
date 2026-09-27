/** Backend graph nodes in the advisor's language. Keys match `app/graph/builder.py`. */
export const NODE_LABELS: Record<string, string> = {
  run: "Run",
  research_company: "Researching company",
  market_intelligence: "Market context",
  confirm_context: "Context check",
  map_exposures: "Mapping exposures",
  policy_intelligence: "Reading the brochures",
  compare_policies: "Comparing policies",
  policy_fit_arena: "Calculating recommendation",
  policy_check: "Running Policy Check",
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
  research_company: "Searching public sources for this company. A missing page does not mean research was skipped.",
  market_intelligence: "Reading industry context. This does not change the policy score.",
  confirm_context: "Checking whether the deck would rest on assumptions alone.",
  map_exposures: "Turning what we know about the client into health-cover needs.",
  policy_intelligence: "Reading each selected brochure on its own. A missing passage stays not established.",
  compare_policies: "Comparing the four policies on the client's requirements.",
  policy_fit_arena: "Calculating the recommendation from the documented evidence.",
  policy_check: "Stress-testing the recommendation before it reaches the client.",
  confirm_recommendation: "Checking whether the result needs your call.",
  evidence_pack: "Assembling the only facts the writer may cite, each with its brochure page.",
  generate_pitch: "Drafting the slides. Every policy statement must cite an evidence id.",
  audit_pitch: "Five auditors test each claim: citation, numbers, exclusions, contradictions, coverage.",
  export_outputs: "Writing the editable PPTX deck and the claim-level audit report.",
  finalize_rejected: "Closing the run without a deck.",
};
