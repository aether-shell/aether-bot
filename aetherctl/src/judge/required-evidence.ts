import type { Phase } from "../types/manifest.js";
import type { RiskLevel } from "../types/integration.js";

/**
 * Required evidence matrix by phase (V3 §6.9).
 * Judge only checks artifacts listed here; extras are checked if present but not required.
 */
const EVIDENCE_MATRIX: Record<Phase, string[]> = {
  "1a": [
    "freeze.json",
    "integration.json",
    "test-results/lint.json",
    "test-results/unit.json",
  ],
  "1b": [
    "freeze.json",
    "integration.json",
    "test-results/lint.json",
    "test-results/unit.json",
    "test-results/integration.json",
    "test-results/e2e.json",
  ],
  "2": [
    "freeze.json",
    "integration.json",
    "test-results/lint.json",
    "test-results/unit.json",
    "test-results/integration.json",
    "test-results/e2e.json",
    "benchmark/*.json",
  ],
  "3": [
    "freeze.json",
    "integration.json",
    "test-results/lint.json",
    "test-results/unit.json",
    "test-results/integration.json",
    "test-results/e2e.json",
    "benchmark/*.json",
    "resilience/*.json",
    "canary/health-samples.json",
  ],
};

/** Additional evidence required for medium+ risk in certain phases. */
const RISK_CONDITIONAL: Record<Phase, Record<RiskLevel, string[]>> = {
  "1a": { low: [], medium: [], high: [] },
  "1b": { low: [], medium: ["resilience/*.json"], high: ["resilience/*.json"] },
  "2": { low: [], medium: ["resilience/*.json"], high: ["resilience/*.json"] },
  "3": { low: [], medium: [], high: [] }, // already required for all
};

/**
 * Get required evidence for a given phase and risk level.
 */
export function getRequiredEvidence(phase: Phase, riskLevel: RiskLevel = "low"): string[] {
  const base = EVIDENCE_MATRIX[phase] ?? [];
  const conditional = RISK_CONDITIONAL[phase]?.[riskLevel] ?? [];
  // Deduplicate
  return [...new Set([...base, ...conditional])];
}

/**
 * Check if a phase requires manifest signature.
 * Phase 1b+ requires signature.
 */
export function requiresSignature(phase: Phase): boolean {
  return phase !== "1a";
}
