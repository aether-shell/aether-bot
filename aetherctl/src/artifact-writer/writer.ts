import fs from "node:fs";
import path from "node:path";
import { computeSha256 } from "./checksum.js";
import { buildManifest, getRequiredEvidence } from "./manifest-builder.js";
import type { ManifestArtifact, Phase, SchemaRegistry } from "../types/manifest.js";
import type { Manifest } from "../types/manifest.js";

export type WriteArtifactInput = {
  /** Relative path within the artifacts directory (e.g., "freeze.json"). */
  relativePath: string;
  /** JSON content to write. */
  content: unknown;
  /** Schema name this artifact conforms to. */
  schema: string;
  /** Who produced this artifact (e.g., "aether-freeze"). */
  producedBy: string;
  /** Original source format if adapted (e.g., "junit_xml"). */
  sourceFormat?: string;
};

/**
 * Artifact Writer — manages the artifacts directory for an incubation run.
 * Writes individual artifacts and generates the final manifest.
 */
export class ArtifactWriter {
  private artifacts: ManifestArtifact[] = [];
  private readonly artifactsDir: string;

  constructor(
    private readonly baseDir: string,
    private readonly incubationId: string,
  ) {
    this.artifactsDir = path.join(baseDir, incubationId);
  }

  /** Ensure the artifacts directory exists. */
  init(): void {
    fs.mkdirSync(this.artifactsDir, { recursive: true });
  }

  /** Write a single artifact file and track it. */
  writeArtifact(input: WriteArtifactInput): string {
    const fullPath = path.join(this.artifactsDir, input.relativePath);

    // Ensure subdirectory exists
    fs.mkdirSync(path.dirname(fullPath), { recursive: true });

    const json = JSON.stringify(input.content, null, 2);
    fs.writeFileSync(fullPath, json, "utf8");

    const sha256 = computeSha256(fullPath);
    const stat = fs.statSync(fullPath);

    const artifact: ManifestArtifact = {
      path: input.relativePath,
      schema: input.schema,
      sha256,
      produced_by: input.producedBy,
      produced_at: new Date().toISOString(),
      source_format: input.sourceFormat,
    };

    this.artifacts.push(artifact);
    return fullPath;
  }

  /** Generate and write the manifest.json file. Returns the manifest. */
  writeManifest(opts: {
    phase: Phase;
    id_composition: { branch: string; base_sha: string; timestamp: string; run_seq: string };
    schema_versions: SchemaRegistry;
  }): Manifest {
    const manifest = buildManifest({
      incubation_id: this.incubationId,
      phase: opts.phase,
      id_composition: opts.id_composition,
      schema_versions: opts.schema_versions,
      artifacts: this.artifacts,
    });

    const manifestPath = path.join(this.artifactsDir, "manifest.json");
    fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf8");

    return manifest;
  }

  /** Get the artifacts directory path. */
  getArtifactsDir(): string {
    return this.artifactsDir;
  }

  /** Get all tracked artifacts. */
  getArtifacts(): ManifestArtifact[] {
    return [...this.artifacts];
  }
}
