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
  intake: Intake;
  policy_ids?: string[];
  profile?: CompanyProfile;
  exposures?: Exposure[];
  fits?: { policy_id: string; score: number; confidence: string; close_call_with: string[] }[];
  recommendation?: Recommendation;
  evidence_pack?: EvidencePack;
  pitch?: Pitch;
  pitch_warnings?: string[];
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

export interface RunState {
  run: RunSummary & { outputs?: Record<string, string> };
  values: RunValues;
  pending: string[];
  question?: Question | null;
  events: RunEvent[];
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
  llm_provider: "gemini";
  llm_model: string;
  /** Whether GEMINI_MODEL is on Google's published free-tier list. */
  model_free_tier_known: boolean;
  research_configured: boolean;
  embedding_provider?: string;
  retrieval: { ready: boolean; chunks?: number; policies?: PolicyDocument[]; embedding_model?: string; reranker?: string | null };
}
