import type { ChangeType } from "../types/integration.js";
import type { ThresholdsConfig, ThresholdSet } from "../types/config.js";

export type ThresholdInput = {
  baseline: {
    total: number;
    passed: number;
    failed: number;
    duration_ms: number;
  };
  candidate: {
    total: number;
    passed: number;
    failed: number;
    duration_ms: number;
  };
  change_type: ChangeType;
};

export type ThresholdViolation = {
  metric: string;
  threshold: number;
  actual: number;
};

export type ThresholdResult = {
  pass: boolean;
  deltas: {
    functionality_delta_pct: number;
    stability_delta_pct: number;
    p95_latency_delta_pct: number;
  };
  violations: ThresholdViolation[];
};

/** Default thresholds if config is not provided. */
const DEFAULT_THRESHOLDS: ThresholdSet = {
  functionality_min_pct: 0,
  stability_max_pct: 0,
  p95_latency_max_pct: 10,
};

/**
 * Compare candidate test results against baseline using configured thresholds.
 *
 * - functionality_delta_pct: (candidate_pass_rate - baseline_pass_rate) * 100
 *   Must be >= -threshold (negative means regression)
 * - stability_delta_pct: (candidate_fail_rate - baseline_fail_rate) * 100
 *   Must be <= threshold
 * - p95_latency_delta_pct: ((candidate_duration - baseline_duration) / baseline_duration) * 100
 *   Must be <= threshold
 */
export function checkThresholds(
  input: ThresholdInput,
  thresholds?: ThresholdsConfig,
): ThresholdResult {
  const t = thresholds?.[input.change_type] ?? DEFAULT_THRESHOLDS;
  const violations: ThresholdViolation[] = [];

  // Functionality delta (pass rate change)
  const basePassRate = input.baseline.total > 0 ? input.baseline.passed / input.baseline.total : 1;
  const candPassRate = input.candidate.total > 0 ? input.candidate.passed / input.candidate.total : 1;
  const funcDelta = (candPassRate - basePassRate) * 100;

  if (funcDelta < -Math.abs(t.functionality_min_pct)) {
    violations.push({
      metric: "functionality_delta_pct",
      threshold: -Math.abs(t.functionality_min_pct),
      actual: funcDelta,
    });
  }

  // Stability delta (fail rate change)
  const baseFailRate = input.baseline.total > 0 ? input.baseline.failed / input.baseline.total : 0;
  const candFailRate = input.candidate.total > 0 ? input.candidate.failed / input.candidate.total : 0;
  const stabilityDelta = (candFailRate - baseFailRate) * 100;

  if (stabilityDelta > t.stability_max_pct) {
    violations.push({
      metric: "stability_delta_pct",
      threshold: t.stability_max_pct,
      actual: stabilityDelta,
    });
  }

  // Latency delta
  const baseDuration = input.baseline.duration_ms || 1; // avoid division by zero
  const latencyDelta = ((input.candidate.duration_ms - input.baseline.duration_ms) / baseDuration) * 100;

  if (latencyDelta > t.p95_latency_max_pct) {
    violations.push({
      metric: "p95_latency_delta_pct",
      threshold: t.p95_latency_max_pct,
      actual: latencyDelta,
    });
  }

  return {
    pass: violations.length === 0,
    deltas: {
      functionality_delta_pct: Math.round(funcDelta * 100) / 100,
      stability_delta_pct: Math.round(stabilityDelta * 100) / 100,
      p95_latency_delta_pct: Math.round(latencyDelta * 100) / 100,
    },
    violations,
  };
}
