import type { Manifest } from "../types/manifest.js";
import type { JudgeReport, JudgeDecision, Verification } from "../types/judge-report.js";
import type { ChangeType, RiskLevel } from "../types/integration.js";
import type { ThresholdsConfig } from "../types/config.js";
import type { Baseline } from "../types/baseline.js";
import { runFailClosedChecks, type FailClosedViolation } from "./fail-closed.js";
import { getRequiredEvidence } from "./required-evidence.js";
import { checkThresholds } from "./threshold-checker.js";
import { BaselineManager } from "./baseline-manager.js";

export type JudgeInput = {
  manifest: Manifest | null;
  actualChecksums: Map<string, string>;
  schemaResults: Map<string, { valid: boolean; path: string }>;
  testResults: Array<{ path: string; total: number; passed: number; failed: number; skipped: number }>;
  signatureValid: boolean | null;
  verification: Verification;
  changeSummary: {
    source_branch: string;
    change_type: ChangeType;
    files_changed: string[];
    description: string;
  };
  riskLevel: RiskLevel;
  riskReason?: string;
  riskOverridden?: boolean;
  baseline: Baseline | null;
  thresholds?: ThresholdsConfig;
  evidenceArtifactPaths: string[];
};

export type JudgeOutput = {
  report: JudgeReport;
  violations: FailClosedViolation[];
};

/**
 * Main Judge — orchestrates the full verdict pipeline.
 *
 * 1. Determine required evidence for the phase
 * 2. Run fail-closed checks
 * 3. If no violations, run threshold comparison against baseline
 * 4. Generate judge-report.json
 */
export function judge(input: JudgeInput): JudgeOutput {
  const manifest = input.manifest;
  const phase = manifest?.phase ?? "1a";

  // Step 1: Get required evidence
  const requiredEvidence = getRequiredEvidence(phase, input.riskLevel);

  // Step 2: Fail-closed checks
  const violations = runFailClosedChecks({
    manifest,
    requiredEvidence,
    actualChecksums: input.actualChecksums,
    schemaResults: input.schemaResults,
    testResults: input.testResults,
    signatureValid: input.signatureValid,
  });

  let decision: JudgeDecision = "promote";
  const rejectionReasons: string[] = [];
  const fixSuggestions: string[] = [];

  if (violations.length > 0) {
    decision = "reject";
    for (const v of violations) {
      rejectionReasons.push(v.reason);
    }
  }

  // Step 3: Threshold comparison (only if fail-closed passed and baseline exists)
  let comparison = null;
  if (violations.length === 0 && input.baseline) {
    const unitBaseline = input.baseline.tests.unit ?? { total: 0, passed: 0, failed: 0, duration_ms: 0 };

    // Find unit test results from verification
    const unitCandidate = {
      total: input.verification.unit_tests.total,
      passed: input.verification.unit_tests.passed,
      failed: input.verification.unit_tests.failed,
      duration_ms: input.verification.unit_tests.duration_ms,
    };

    const thresholdResult = checkThresholds(
      { baseline: unitBaseline, candidate: unitCandidate, change_type: input.changeSummary.change_type },
      input.thresholds,
    );

    comparison = {
      functionality_delta_pct: thresholdResult.deltas.functionality_delta_pct,
      error_rate_delta: thresholdResult.deltas.stability_delta_pct,
      p95_latency_delta_pct: thresholdResult.deltas.p95_latency_delta_pct,
      security_critical_count: 0,
      cost_delta_pct: 0,
    };

    if (!thresholdResult.pass) {
      decision = "reject";
      for (const v of thresholdResult.violations) {
        rejectionReasons.push(`threshold_violation: ${v.metric} (${v.actual.toFixed(2)}% exceeds ${v.threshold}%)`);
        fixSuggestions.push(`Improve ${v.metric}: current delta is ${v.actual.toFixed(2)}%, threshold is ${v.threshold}%`);
      }
    }
  }

  // Step 4: Check if any test failed
  if (violations.length === 0 && !input.verification.unit_tests.pass) {
    decision = "reject";
    rejectionReasons.push("unit_tests_failed");
  }

  if (violations.length === 0 && input.verification.lint && !input.verification.lint.pass) {
    decision = "reject";
    rejectionReasons.push("lint_failed");
  }

  // Build report
  const report: JudgeReport = {
    incubation_id: manifest?.incubation_id ?? "unknown",
    timestamp: new Date().toISOString(),
    phase,
    change_summary: input.changeSummary,
    risk_level: input.riskLevel,
    risk_classification_reason: input.riskReason,
    risk_overridden: input.riskOverridden ?? false,
    baseline: input.baseline
      ? { main_sha: input.baseline.main_sha, lockfile_hash: "", schema_version: "1.0.0" }
      : null,
    verification: input.verification,
    comparison,
    decision,
    rejection_reasons: rejectionReasons,
    fix_suggestions: fixSuggestions,
    evidence_artifact_paths: input.evidenceArtifactPaths,
  };

  return { report, violations };
}
