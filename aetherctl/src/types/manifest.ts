import type { ChangeType, RiskLevel } from "./integration.js";

/** Incubation manifest — integrity anchor for all artifacts. (V3 §6.7) */
export type IdComposition = {
  branch: string;
  base_sha: string;
  timestamp: string;
  run_seq: string;
};

export type SchemaRegistry = {
  manifest: string;
  freeze: string;
  integration: string;
  test_result: string;
  benchmark?: string;
  judge_report: string;
};

export type ManifestArtifact = {
  path: string;
  schema: string;
  sha256: string;
  produced_by: string;
  produced_at: string;
  source_format?: string;
};

export type Phase = "1a" | "1b" | "2" | "3";

export type Manifest = {
  incubation_id: string;
  created_at: string;
  phase: Phase;
  id_composition: IdComposition;
  schema_registry: SchemaRegistry;
  required_evidence: string[];
  artifacts: ManifestArtifact[];
  manifest_signature?: string | null;
};
