/** Incubation state — state machine status for an incubation run. (V3 §4) */
export type IncubationStep =
  | "freeze"
  | "integrate"
  | "twin_up"
  | "data_mirror"
  | "regress"
  | "resilience"
  | "judge"
  | "promote"
  | "canary"
  | "done";

export type StepStatus = "pending" | "running" | "ok" | "failed" | "skipped" | "timeout";

export type StepRecord = {
  step: IncubationStep;
  status: StepStatus;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
};

export type IncubationState = {
  incubation_id: string;
  phase: string;
  current_step: IncubationStep;
  steps: StepRecord[];
  created_at: string;
  updated_at: string;
};
