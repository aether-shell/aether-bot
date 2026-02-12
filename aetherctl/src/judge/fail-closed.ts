import type { Phase } from "../types/manifest.js";
import type { Manifest, ManifestArtifact } from "../types/manifest.js";
import { requiresSignature } from "./required-evidence.js";

export type FailClosedViolation = {
  rule: number;
  reason: string;
};

/**
 * Rule 1: manifest.json must exist.
 */
export function checkManifestExists(manifest: Manifest | null): FailClosedViolation | null {
  if (!manifest) {
    return { rule: 1, reason: "missing_manifest" };
  }
  return null;
}

/**
 * Rule 2: All required_evidence artifacts must be present in manifest.
 */
export function checkRequiredArtifacts(
  requiredEvidence: string[],
  artifacts: ManifestArtifact[],
): FailClosedViolation[] {
  const artifactPaths = new Set(artifacts.map((a) => a.path));
  const violations: FailClosedViolation[] = [];

  for (const required of requiredEvidence) {
    // Handle glob patterns (e.g., "benchmark/*.json")
    if (required.includes("*")) {
      const prefix = required.split("*")[0];
      const hasMatch = [...artifactPaths].some((p) => p.startsWith(prefix));
      if (!hasMatch) {
        violations.push({ rule: 2, reason: `missing_artifact: ${required}` });
      }
    } else if (!artifactPaths.has(required)) {
      violations.push({ rule: 2, reason: `missing_artifact: ${required}` });
    }
  }

  return violations;
}

/**
 * Rule 3: Artifact sha256 must match manifest declaration.
 * Takes a map of path → actual sha256.
 */
export function checkIntegrity(
  artifacts: ManifestArtifact[],
  actualChecksums: Map<string, string>,
): FailClosedViolation[] {
  const violations: FailClosedViolation[] = [];

  for (const artifact of artifacts) {
    const actual = actualChecksums.get(artifact.path);
    if (actual && actual !== artifact.sha256) {
      violations.push({ rule: 3, reason: `integrity_mismatch: ${artifact.path}` });
    }
  }

  return violations;
}

/**
 * Rule 4: Artifact must conform to its declared schema.
 * Takes a map of path → schema validation result.
 */
export function checkSchemaValidity(
  schemaResults: Map<string, { valid: boolean; path: string }>,
): FailClosedViolation[] {
  const violations: FailClosedViolation[] = [];

  for (const [artifactPath, result] of schemaResults) {
    if (!result.valid) {
      violations.push({ rule: 4, reason: `schema_invalid: ${artifactPath}` });
    }
  }

  return violations;
}

/**
 * Rule 5: Data must not be self-contradictory (total < passed + failed).
 */
export function checkDataConsistency(
  testResults: Array<{ path: string; total: number; passed: number; failed: number; skipped: number }>,
): FailClosedViolation[] {
  const violations: FailClosedViolation[] = [];

  for (const tr of testResults) {
    if (tr.total < tr.passed + tr.failed) {
      violations.push({ rule: 5, reason: `contradictory_evidence: ${tr.path}` });
    }
  }

  return violations;
}

/**
 * Rule 6: Manifest signature must be valid (Phase 1b+ only).
 */
export function checkSignatureValid(
  phase: Phase,
  signatureValid: boolean | null,
): FailClosedViolation | null {
  if (!requiresSignature(phase)) return null;
  if (signatureValid === false) {
    return { rule: 6, reason: "signature_invalid" };
  }
  return null;
}

/**
 * Rule 7: Manifest signature must exist (Phase 1b+ only).
 */
export function checkSignatureExists(
  phase: Phase,
  signature: string | null | undefined,
): FailClosedViolation | null {
  if (!requiresSignature(phase)) return null;
  if (!signature) {
    return { rule: 7, reason: "missing_signature" };
  }
  return null;
}

/**
 * Run all fail-closed checks and return all violations.
 */
export function runFailClosedChecks(opts: {
  manifest: Manifest | null;
  requiredEvidence: string[];
  actualChecksums: Map<string, string>;
  schemaResults: Map<string, { valid: boolean; path: string }>;
  testResults: Array<{ path: string; total: number; passed: number; failed: number; skipped: number }>;
  signatureValid: boolean | null;
}): FailClosedViolation[] {
  const violations: FailClosedViolation[] = [];

  // Rule 1
  const r1 = checkManifestExists(opts.manifest);
  if (r1) {
    violations.push(r1);
    return violations; // Can't check further without manifest
  }

  const manifest = opts.manifest!;

  // Rule 2
  violations.push(...checkRequiredArtifacts(opts.requiredEvidence, manifest.artifacts));

  // Rule 3
  violations.push(...checkIntegrity(manifest.artifacts, opts.actualChecksums));

  // Rule 4
  violations.push(...checkSchemaValidity(opts.schemaResults));

  // Rule 5
  violations.push(...checkDataConsistency(opts.testResults));

  // Rule 6
  const r6 = checkSignatureValid(manifest.phase, opts.signatureValid);
  if (r6) violations.push(r6);

  // Rule 7
  const r7 = checkSignatureExists(manifest.phase, manifest.manifest_signature);
  if (r7) violations.push(r7);

  return violations;
}
