import type { ChangeType, RiskLevel } from "./integration.js";
import type { Phase } from "./manifest.js";

/** Judge report — final verdict on an incubation. (V3 §6.11) */
export type LintVerification = {
  pass: boolean;
  errors?: number;
  warnings?: number;
};

export type TestVerification = {
  pass: boolean;
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
};

export type NullableTestVerification = {
  pass?: boolean | null;
  total?: number | null;
  passed?: number | null;
  failed?: number | null;
  skipped?: number | null;
  duration_ms?: number | null;
} | null;

export type Verification = {
  lint: LintVerification;
  unit_tests: TestVerification;
  integration_tests?: TestVerification;
  e2e_tests?: NullableTestVerification;
  resilience?: { pass?: boolean | null; details?: unknown } | null;
};

export type Comparison = {
  functionality_delta_pct?: number;
  error_rate_delta?: number;
  p95_latency_delta_pct?: number;
  security_critical_count?: number;
  cost_delta_pct?: number;
} | null;

export type Migration = {
  has_schema_change?: boolean;
  reversible?: boolean;
  rollback_sql?: string | null;
} | null;

export type JudgeDecision = "promote" | "reject";

export type JudgeReport = {
  incubation_id: string;
  timestamp: string;
  phase: Phase;
  change_summary: {
    source_branch: string;
    change_type: ChangeType;
    files_changed: string[];
    description: string;
  };
  risk_level: RiskLevel;
  risk_classification_reason?: string;
  risk_overridden?: boolean;
  baseline?: {
    main_sha: string;
    lockfile_hash: string;
    schema_version: string;
  } | null;
  verification: Verification;
  comparison?: Comparison;
  migration?: Migration;
  decision: JudgeDecision;
  rejection_reasons?: string[];
  fix_suggestions?: string[];
  conflict_notes?: string[];
  evidence_artifact_paths: string[];
};
