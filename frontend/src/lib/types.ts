// Shapes mirrored from backend Pydantic models (app/models/*). Kept loose where the UI only displays.
type CoverageStatus = "COVERED" | "PARTIALLY_COVERED" | "CONDITIONAL" | "EXCLUDED" | "ADD_ON" | "NOT_FOUND" | "UNKNOWN";
export type AuditStatus = "SUPPORTED" | "PARTIALLY_SUPPORTED" | "CONTRADICTED" | "NOT_FOUND" | "UNCERTAIN" | "NOT_APPLICABLE";
type FactKind = "FACT" | "INFERENCE" | "ASSUMPTION" | "UNKNOWN";

export interface PolicyDocument {
  policy_id: string;
  policy_name: string;
  insurer: string;
  product_uin?: string | null;
  file_name: string;
  pages: number;
  variants: string[];
  short_label?: string | null;
  page_flags?: Record<string, string>;
}

export interface SourceRef {
  policy_id: string;
  policy_name?: string | null;
  chunk_id: string;
  page: number;
  section?: string | null;
  clause?: string | null;
  content_type?: string | null;
  source_text: string;
  retrieval_relevance?: number | null;
  linked_conditions: string[];
}

interface WebSource {
  url: string;
  title?: string | null;
  published_date?: string | null;
  snippet?: string | null;
}

interface ClientFact {
  fact_id: string;
  field: string;
  text: string;
  kind: FactKind;
  sources: WebSource[];
  confidence: number;
}

interface CompanyProfile {
  company_name: string;
  overview?: string | null;
  industry?: string | null;
  size?: string | null;
  geography?: string | null;
  workforce?: string | null;
  business_characteristics: string[];
  key_risks: string[];
  facts: ClientFact[];
  research_status: string;
  research_note?: string | null;
}

interface Exposure {
  exposure_id: string;
  title: string;
  description: string;
  basis: string[];
  reasoning: string;
  status: FactKind;
  confidence: number;
  priority: number;
  feature_keys: string[];
}

interface Recommendation {
  recommended_policy_id: string;
  policy_name: string;
  fit_score: number;
  rationale: string[];
  runner_up_policy_id?: string | null;
  caveats: string[];
  assumptions: string[];
}

interface EvidenceItem {
  evidence_id: string;
  policy_id: string;
  policy_name: string;
  feature_key: string;
  feature_label: string;
  status: CoverageStatus;
  statement: string;
  conditions: string[];
  exclusions: string[];
  variant_scope?: string | null;
  sources: SourceRef[];
}

interface EvidencePack {
  recommended_policy_id: string;
  items: EvidenceItem[];
  comparison_notes: string[];
  gaps: string[];
  assumptions: string[];
  company_inferences: string[];
}

export interface SlideBullet {
  text: string;
  source_chunk_ids: string[];
  /** Web pages behind a company statement; linked in the exported deck. */
  source_urls?: string[];
  policy_id?: string | null;
  kind: string;
}

export interface Slide {
  slide_number: number;
  title: string;
  subtitle?: string | null;
  bullets: SlideBullet[];
  footnote?: string | null;
  layout: string;
}

export interface Pitch {
  pitch_id: string;
  company_name: string;
  recommended_policy_id: string;
  slides: Slide[];
  version: number;
  disclaimer: string;
}

interface Claim {
  claim_id: string;
  claim_text: string;
  claim_type: string;
  slide: number;
  bullet_index?: number | null;
  policy_id?: string | null;
  feature_key?: string | null;
  numbers: string[];
  material: boolean;
}

interface CheckResult {
  check: string;
  passed: boolean | null;
  status: AuditStatus;
  detail: string;
}

interface EvidencePassport {
  claim_id: string;
  claim_text: string;
  policy_id?: string | null;
  policy_name?: string | null;
  page?: number | null;
  section?: string | null;
  clause?: string | null;
  source_text?: string | null;
  retrieval_relevance?: number | null;
  verification: AuditStatus;
  audit: string;
  linked_conditions: string[];
}

export interface ClaimAudit {
  claim: Claim;
  status: AuditStatus;
  checks: CheckResult[];
  evidence: SourceRef[];
  passport: EvidencePassport;
  action: string;
  correction_hint?: string | null;
}

interface AuditSummary {
  total_claims: number;
  material_claims: number;
  supported: number;
  partially_supported: number;
  contradicted: number;
  not_found: number;
  uncertain: number;
  not_applicable: number;
  gate: "PASS" | "FAIL" | "UNCERTAIN";
  confidence_score: number;
}

export interface AuditReport {
  audit_id: string;
  pitch_id: string;
  pitch_version: number;
  summary: AuditSummary;
  claims: ClaimAudit[];
  major_policy_gaps: string[];
  generated_at: string;
  note: string;
}

export interface RunEvent {
  node: string;
  status: string;
  message?: string | null;
  created_at: string;
}

export interface RunSummary {
  run_id: string;
  company_name: string;
  status: string;
  created_at: string;
  updated_at: string;
  recommended_policy_id?: string | null;
  audit_gate?: string | null;
  current_node?: string | null;
  error?: string | null;
  /** "quota" when the Gemini free-tier limit stopped the run; "interrupted" after a server restart; "error" otherwise. */
  error_kind?: "quota" | "interrupted" | "error" | null;
  /** Seconds until the quota window is expected to reset (quota errors only). */
  retry_after?: number | null;
  retryable?: boolean | null;
  retries?: number | null;
}

export interface Intake {
  company_name: string;
  industry?: string | null;
  geography?: string | null;
  employee_count?: number | null;
  advisor_notes?: string | null;
  client_priorities?: string[];
}

interface RunValues {
  run_id: string;
  /** Absent until the graph checkpoint is written, and on runs interrupted before the first checkpoint. */
  intake?: Intake | null;
  policy_ids?: string[];
  profile?: CompanyProfile;
  exposures?: Exposure[];
  fits?: { policy_id: string; score: number; confidence: string; close_call_with: string[] }[];
  recommendation?: Recommendation;
  evidence_pack?: EvidencePack;
  pitch?: Pitch;
  pitch_warnings?: string[];
  pitch_stale?: boolean;
  audit?: AuditReport;
  audit_history?: (AuditSummary & { pitch_version: number })[];
  review?: { action: string; reviewer?: string | null; note?: string | null; feedback?: string | null };
  status?: string;
  outputs?: Record<string, string>;
  error?: string | null;
}

interface QuestionOption {
  id: string;
  label: string;
  /** close-call options carry the fit summary of that policy */
  score?: number;
  confidence?: string;
  explanation?: string[];
}

/** What the run is paused on. Mirrors the payload built by `ask()` in the graph. */
export interface Question {
  question: "context" | "close_call" | "review";
  run_id: string;
  message: string;
  options: QuestionOption[];
  fields?: string[];
  recommended?: string;
  pitch_version?: number;
  audit_gate?: string | null;
}

export interface AdvisorFact {
  text: string;
  field?: string | null;
  label: "VERIFIED" | "ASSUMPTION" | "UNKNOWN" | string;
  wording?: string;
}

export interface AdvisorView {
  error?: string;
  company?: {
    company_name?: string | null;
    industry?: string | null;
    size?: string | null;
    footprint?: string | null;
    characteristics: string[];
    research_status?: string | null;
    research_note?: string | null;
    facts: AdvisorFact[];
    exposures: { title: string; description: string; label: string; wording?: string; rationale: string }[];
    market?: { status: string; note: string; context: string; hypotheses: string[] };
  } | null;
  recommendation?: {
    automatic: boolean;
    policy_id: string;
    policy_name?: string | null;
    fit_score?: number | null;
    evidence_completeness?: number | null;
    confidence?: string | null;
    decision_state: string;
    decision_label: string;
    wording: string;
    baseline_only?: boolean;
    drivers: string[];
    gaps: string[];
    changed_after_check: boolean;
    scores: { policy_id: string; policy_name?: string | null; fit_score?: number | null; evidence_completeness?: number | null; confidence?: string | null; decision_label: string; decision_sufficient?: boolean }[];
    requirements?: { feature: string; label: string; concept?: string | null; weight?: number | null; results: { policy_id?: string | null; policy_name?: string | null; criterion_score?: number | null; status: string; label: string }[] }[];
    alternatives?: { policy_id?: string | null; policy_name?: string | null; fit_score?: number | null; evidence_completeness?: number | null; decision_state?: string | null; strong_matches: string[]; trade_offs: string[]; evidence: string[] }[];
  } | null;
  why?: { requirement: string; feature?: string | null; priority: string; result: string; page?: number | null; policy_name?: string | null; impact: string; contribution?: number | null; weight?: number | null; chunk_id?: string | null }[];
  comparison?: {
    policies: { policy_id: string; policy_name?: string | null; insurer?: string | null }[];
    baseline?: boolean;
    rows: { feature: string; label: string; cells: { policy_id: string; policy_name?: string | null; state?: string; status_label: string; page?: number | null; section?: string | null }[] }[];
  } | null;
  policy_check?: {
    status: string;
    stability: string;
    challenge_found: boolean;
    challenge?: { requirement: string; feature?: string | null; policy_id?: string | null; policy_name?: string | null; evidence_id?: string | null; explanation: string; materiality: string } | null;
    scenarios: { scenario: string; requirement: string; feature?: string | null; outcomes: { policy_id?: string | null; policy_name?: string | null; result: string; evidence?: string | null; limitation?: string | null }[]; limitation?: string | null }[];
    gaps: { kind: string; meaning: string; feature: string; policy_name?: string | null; detail: string; evidence?: string | null }[];
    note: string;
    checked: { challenge: boolean; scenarios: boolean; gaps: boolean };
  } | null;
  audit?: { supported: number; review: number; contradicted: number; not_found: number; gate?: string | null; status_label: string } | null;
}

export interface ScenarioCell {
  policy_id: string;
  policy_name: string;
  feature: string;
  state: string;
  label: string;
  explanation?: string | null;
  quote?: string | null;
  page?: number | null;
  section?: string | null;
  chunk_id?: string | null;
  conditions: string[];
}

export interface ScenarioResult {
  ok: boolean;
  code: string;
  message: string;
  scenario?: string;
  features: string[];
  rows: { feature: string; label: string; cells: ScenarioCell[] }[];
  changes_recommendation: boolean;
}

export interface StudioProposal {
  ok: boolean;
  intent?: string | null;
  wording?: boolean;
  message: string;
  slide: Slide;
  changes?: { index: number; before: string; after: string }[];
  locks?: { FACT_LOCK?: boolean; NUMBER_LOCK?: boolean; POLICY_LOCK?: boolean; RECOMMENDATION_LOCK?: string; EVIDENCE_LOCK?: string[]; AUDIT_LOCK?: string };
  acceptable?: boolean;
  audit?: { gate?: string | null; supported: number; contradicted: number; not_found: number };
  audit_blocks_accept?: boolean;
}

export interface RecommendationChangeResult {
  ok: boolean;
  applied: boolean;
  ready_to_apply?: boolean;
  override?: boolean;
  supported?: boolean;
  unchanged?: boolean;
  message: string;
  recommendation?: { recommended_policy_id?: string | null; decision_state?: string | null; fit_score?: number | null; policy_name?: string | null };
  scores?: { policy_id: string; policy_name?: string | null; fit_score?: number | null; decision_state?: string | null }[];
  gaps?: string[];
  interpreted_change?: string[];
  old_weights?: { feature: string; weight: number }[];
  new_weights?: { feature: string; weight: number }[];
  alternatives?: { policy_name?: string | null; fit_score?: number | null; decision_state?: string | null }[];
}

export interface PolicyUploadStatus {
  original_name: string;
  status: string;
  selected: boolean;
  in_comparison: boolean;
  message: string;
}

export interface EvidenceLookup {
  policy_id: string;
  insurer?: string | null;
  product?: string | null;
  source_document?: string | null;
  page?: number | null;
  section?: string | null;
  quote?: string | null;
  feature: string;
  feature_label: string;
  status: string;
  status_label: string;
  conditions: string[];
  chunk_id?: string | null;
}

export interface RunState {
  run: RunSummary & { outputs?: Record<string, string> };
  values: RunValues;
  pending: string[];
  question?: Question | null;
  events: RunEvent[];
  advisor?: AdvisorView | null;
}

export interface Answer {
  action: string;
  slides?: Slide[];
  feedback?: string;
  note?: string;
  reviewer?: string;
  industry?: string;
  geography?: string;
  employee_count?: number;
  advisor_notes?: string;
  client_priorities?: string[];
}

export interface Health {
  status: string;
  llm_configured: boolean;
  llm_provider: "groq" | "gemini";
  llm_model: string;
  /** Whether GEMINI_MODEL is on Google's published free-tier list. */
  model_free_tier_known: boolean;
  research_configured: boolean;
  embedding_provider?: string;
  retrieval: { ready: boolean; chunks?: number; policies?: PolicyDocument[]; embedding_model?: string; reranker?: string | null };
}
