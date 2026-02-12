import type { Manifest, ManifestArtifact, Phase, SchemaRegistry } from "../types/manifest.js";

/** Evidence required by phase. */
const REQUIRED_EVIDENCE_BY_PHASE: Record<Phase, string[]> = {
  "1a": ["freeze.json", "integration.json", "test-results/unit.json", "judge-report.json"],
  "1b": ["freeze.json", "integration.json", "test-results/unit.json", "test-results/integration.json", "judge-report.json"],
  "2": ["freeze.json", "integration.json", "test-results/unit.json", "test-results/integration.json", "test-results/e2e.json", "judge-report.json"],
  "3": ["freeze.json", "integration.json", "test-results/unit.json", "test-results/integration.json", "test-results/e2e.json", "resilience.json", "judge-report.json"],
};

export type ManifestBuildInput = {
  incubation_id: string;
  phase: Phase;
  id_composition: {
    branch: string;
    base_sha: string;
    timestamp: string;
    run_seq: string;
  };
  schema_versions: SchemaRegistry;
  artifacts: ManifestArtifact[];
};

/**
 * Build a complete manifest from the given inputs.
 * Automatically fills required_evidence based on phase.
 */
export function buildManifest(input: ManifestBuildInput): Manifest {
  return {
    incubation_id: input.incubation_id,
    created_at: new Date().toISOString(),
    phase: input.phase,
    id_composition: input.id_composition,
    schema_registry: input.schema_versions,
    required_evidence: REQUIRED_EVIDENCE_BY_PHASE[input.phase] ?? [],
    artifacts: input.artifacts,
    manifest_signature: null,
  };
}

/** Get required evidence list for a given phase. */
export function getRequiredEvidence(phase: Phase): string[] {
  return REQUIRED_EVIDENCE_BY_PHASE[phase] ?? [];
}
