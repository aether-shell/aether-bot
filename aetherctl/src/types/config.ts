/** Configuration types — layered config system. (V3 §12) */
export type Phase = "1a" | "1b" | "2" | "3";

export type StateMachineConfig = {
  steps: string[];
  skip_rules: Record<string, string[]>;
  timeouts: Record<string, number>;
  max_concurrent: number;
};

export type ThresholdSet = {
  functionality_min_pct: number;
  stability_max_pct: number;
  p95_latency_max_pct: number;
};

export type ThresholdsConfig = Record<string, ThresholdSet>;

export type RiskRule = {
  pattern: string;
  level: "low" | "medium" | "high";
};

export type RiskPolicyConfig = {
  path_rules: RiskRule[];
  auto_escalation: string[];
};

export type AetherConfig = {
  schema_version: string;
  phase: Phase;
  artifacts_dir: string;
  required_evidence?: string[];
  state_machine?: StateMachineConfig;
  thresholds?: ThresholdsConfig;
  risk_policy?: RiskPolicyConfig;
};
