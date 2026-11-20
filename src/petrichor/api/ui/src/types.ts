// API payload types (spec §19.4): the server is the source of truth - these
// are read-only views of its responses, never verdicts computed client-side.

export interface ProfileEntry {
  id: string;
  role: string;
  conf: number;
}

export interface Profile {
  proposed: ProfileEntry[];
}

export type CrossStatus = "verified_by_n" | "controversial" | string;

export interface Proof {
  status: string;
  verdict: string | null;
  tool_version: string;
}

export interface Question {
  question_id: string;
  formula: string;
  family: string;
  triage: string;
  status: string;
  cross_status: CrossStatus;
  verified_by?: number;
  proofs: Proof[];
  // P34 (issue #34): the earlier generated question this one converges with,
  // and the surviving question of the human fusion covering this one.
  convergence_hint?: string | null;
  covered_by?: string | null;
}

// P34: one human fusion record (POST/GET /v1/sessions/{sid}/convergences).
export interface ConvergenceRecord {
  session_id: string;
  question_id: string;
  proposition_id: string;
  timestamp: number;
}

export interface ConvergenceList {
  convergences: ConvergenceRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface JournalEntry {
  event: string;
  detail?: Record<string, unknown> | null;
}

// P37 (issue #37): one append-only formula edit record, as returned by
// POST /v1/sessions/{sid}/questions/{qid}/formula.
export interface FormulaEditRecord {
  session_id: string;
  question_id: string;
  old_formula: string;
  new_formula: string;
  timestamp: number;
}

// The inverse reading of a formula: an LLM proposition labeled
// provenance: llm - never a verdict. `degraded` readings carry a server
// note instead of the reading fields.
export interface FormulaReading {
  reading?: string;
  not_says?: string;
  pattern?: string;
  cross?: string;
  provenance?: string;
  degraded?: boolean;
  note?: string;
}

export interface FormulaEditResponse {
  record: FormulaEditRecord;
  reading: FormulaReading | null;
}

export interface Contract {
  questions: Question[];
  journal: JournalEntry[];
  proof_dossiers?: ProofDossier[];
}

// P38 (issue #38): one clickable proof dossier of the contract - a clause
// with its runs (verdict per tool, technique, cost, witness id) and, on
// controversy, the two camps exported as-is (spec §17.4).
export interface ContractProof {
  run_id: string;
  link: string;
  status: string;
  triage: string;
  verdict: string | null;
  tool_version: string;
  technique: string | null;
  cost_tool_seconds: number | null;
  witness_id: string | null;
}

export interface ProofDossier {
  question_id: string;
  formula: string;
  status: string;
  cross_status: string;
  verified_by: number;
  proofs: ContractProof[];
  camps: string[];
}

export interface Budget {
  consumed_seconds: number;
  budget_seconds: number;
  remaining_seconds: number;
}

export interface Session {
  session_id: string;
  model_id: string;
  run_ids: string[];
}

export interface RunOutput {
  verdict?: string;
  witness?: unknown;
}

export interface Run {
  run_id: string;
  family: string;
  kind: string;
  status: string;
  triage: { kind: string };
  output: RunOutput | null;
}

export interface WitnessStep {
  index: number;
  marking: string[];
  fired: string | null;
}

export interface Narration {
  step_index: number;
  text: string;
  provenance: string;
}

export interface Witness {
  witness_id: string;
  kind: string;
  cyclic: boolean;
  back_edge?: string;
  steps: WitnessStep[];
  narrations?: Narration[];
}
