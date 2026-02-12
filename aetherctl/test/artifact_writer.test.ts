import { describe, expect, it, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { computeSha256, computeSha256FromContent } from "../src/artifact-writer/checksum.js";
import { buildManifest, getRequiredEvidence } from "../src/artifact-writer/manifest-builder.js";
import { ArtifactWriter } from "../src/artifact-writer/writer.js";
import { createRegistry } from "../src/schema/registry.js";

const SCHEMA_DIR = path.resolve(import.meta.dirname, "../schemas");

describe("checksum", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-checksum-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("produces deterministic SHA256 for same content", () => {
    const file = path.join(tmpDir, "test.txt");
    fs.writeFileSync(file, "hello world");
    const hash1 = computeSha256(file);
    const hash2 = computeSha256(file);
    expect(hash1).toBe(hash2);
    expect(hash1).toMatch(/^[a-f0-9]{64}$/);
  });

  it("produces different hashes for different content", () => {
    const f1 = path.join(tmpDir, "a.txt");
    const f2 = path.join(tmpDir, "b.txt");
    fs.writeFileSync(f1, "hello");
    fs.writeFileSync(f2, "world");
    expect(computeSha256(f1)).not.toBe(computeSha256(f2));
  });

  it("computes hash from string content", () => {
    const hash = computeSha256FromContent("test");
    expect(hash).toMatch(/^[a-f0-9]{64}$/);
  });
});

describe("manifest-builder", () => {
  it("builds manifest with correct required_evidence for phase 1a", () => {
    const manifest = buildManifest({
      incubation_id: "develop-abc1234-20260209-001",
      phase: "1a",
      id_composition: { branch: "develop", base_sha: "abc1234", timestamp: "20260209", run_seq: "001" },
      schema_versions: { manifest: "1.0.0", freeze: "1.0.0", integration: "1.0.0", test_result: "1.0.0", judge_report: "1.0.0" },
      artifacts: [],
    });
    expect(manifest.phase).toBe("1a");
    expect(manifest.required_evidence).toContain("freeze.json");
    expect(manifest.required_evidence).toContain("judge-report.json");
    expect(manifest.required_evidence).not.toContain("resilience.json");
  });

  it("returns correct required evidence per phase", () => {
    expect(getRequiredEvidence("1a")).toHaveLength(4);
    expect(getRequiredEvidence("1b")).toContain("test-results/integration.json");
    expect(getRequiredEvidence("3")).toContain("resilience.json");
  });

  it("generated manifest passes schema validation", async () => {
    const registry = await createRegistry(SCHEMA_DIR);
    const manifest = buildManifest({
      incubation_id: "develop-abc1234-20260209-001",
      phase: "1a",
      id_composition: { branch: "develop", base_sha: "abc1234", timestamp: "20260209", run_seq: "001" },
      schema_versions: { manifest: "1.0.0", freeze: "1.0.0", integration: "1.0.0", test_result: "1.0.0", judge_report: "1.0.0" },
      artifacts: [
        { path: "freeze.json", schema: "freeze@1.0.0", sha256: "a".repeat(64), produced_by: "aether-freeze", produced_at: new Date().toISOString() },
      ],
    });
    const { valid, errors } = await registry.validate("manifest", manifest);
    expect(errors).toBeNull();
    expect(valid).toBe(true);
  });
});

describe("artifact writer", () => {
  let tmpDir: string;
  let writer: ArtifactWriter;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-writer-"));
    writer = new ArtifactWriter(tmpDir, "test-incubation-001");
    writer.init();
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("creates artifacts directory on init", () => {
    expect(fs.existsSync(writer.getArtifactsDir())).toBe(true);
  });

  it("writes artifact file and tracks it", () => {
    const fullPath = writer.writeArtifact({
      relativePath: "freeze.json",
      content: { incubation_id: "test", created_at: new Date().toISOString() },
      schema: "freeze@1.0.0",
      producedBy: "aether-freeze",
    });

    expect(fs.existsSync(fullPath)).toBe(true);
    expect(writer.getArtifacts()).toHaveLength(1);
    expect(writer.getArtifacts()[0].sha256).toMatch(/^[a-f0-9]{64}$/);
  });

  it("writes nested artifact paths", () => {
    writer.writeArtifact({
      relativePath: "test-results/unit.json",
      content: { pass: true, total: 10 },
      schema: "test-result@1.0.0",
      producedBy: "aether-adapter",
    });

    const expected = path.join(writer.getArtifactsDir(), "test-results/unit.json");
    expect(fs.existsSync(expected)).toBe(true);
  });

  it("generates manifest with all tracked artifacts", () => {
    writer.writeArtifact({
      relativePath: "freeze.json",
      content: { test: true },
      schema: "freeze@1.0.0",
      producedBy: "aether-freeze",
    });

    const manifest = writer.writeManifest({
      phase: "1a",
      id_composition: { branch: "develop", base_sha: "abc1234", timestamp: "20260209", run_seq: "001" },
      schema_versions: { manifest: "1.0.0", freeze: "1.0.0", integration: "1.0.0", test_result: "1.0.0", judge_report: "1.0.0" },
    });

    expect(manifest.artifacts).toHaveLength(1);
    expect(manifest.incubation_id).toBe("test-incubation-001");

    // manifest.json file should exist
    const manifestPath = path.join(writer.getArtifactsDir(), "manifest.json");
    expect(fs.existsSync(manifestPath)).toBe(true);
  });
});
