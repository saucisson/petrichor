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
}

export interface JournalEntry {
  event: string;
  detail?: Record<string, unknown> | null;
}

export interface Contract {
  questions: Question[];
  journal: JournalEntry[];
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
