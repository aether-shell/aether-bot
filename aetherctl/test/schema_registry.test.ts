import { describe, expect, it, beforeAll } from "vitest";
import path from "node:path";
import { SchemaRegistry, createRegistry } from "../src/schema/registry.js";

const SCHEMA_DIR = path.resolve(import.meta.dirname, "../schemas");

describe("schema registry", () => {
  let registry: SchemaRegistry;

  beforeAll(async () => {
    registry = await createRegistry(SCHEMA_DIR);
  });

  it("discovers all schema files", () => {
    const names = registry.names();
    expect(names).toContain("freeze");
    expect(names).toContain("integration");
    expect(names).toContain("manifest");
    expect(names).toContain("test-result");
    expect(names).toContain("baseline");
    expect(names).toContain("judge-report");
    expect(names).toContain("adapter-output");
  });

  it("returns version registry", () => {
    const versions = registry.versions();
    expect(typeof versions.freeze).toBe("string");
    expect(typeof versions.integration).toBe("string");
  });

  describe("freeze schema", () => {
    it("accepts valid freeze", async () => {
      const { valid } = await registry.validate("freeze", {
        incubation_id: "develop-a1b2c3d-20260209-001",
        created_at: "2026-02-09T10:00:00Z",
        main_sha: "a1b2c3d",
        lockfile_hash: "sha256:abc123",
        schema_version: "1.0.0",
        baseline_ref: null,
      });
      expect(valid).toBe(true);
    });

    it("rejects freeze missing required fields", async () => {
      const { valid } = await registry.validate("freeze", {
        incubation_id: "test",
      });
      expect(valid).toBe(false);
    });
  });

  describe("integration schema", () => {
    it("accepts valid integration", async () => {
      const { valid } = await registry.validate("integration", {
        incubation_id: "develop-a1b2c3d-20260209-001",
        created_at: "2026-02-09T10:00:00Z",
        source_branch: "feature/foo",
        target_branch: "develop",
        merge_strategy: "merge",
        conflicts: [],
        risk_level: "low",
        risk_override: { overridden: false },
        change_type: "feature",
        files_changed: ["src/foo.ts"],
        lines_added: 10,
        lines_removed: 2,
      });
      expect(valid).toBe(true);
    });

    it("rejects integration with invalid merge_strategy", async () => {
      const { valid } = await registry.validate("integration", {
        incubation_id: "test",
        created_at: "2026-02-09T10:00:00Z",
        source_branch: "feature/foo",
        target_branch: "develop",
        merge_strategy: "squash",
        risk_level: "low",
        change_type: "feature",
        files_changed: [],
        lines_added: 0,
        lines_removed: 0,
      });
      expect(valid).toBe(false);
    });
  });

  describe("test-result schema", () => {
    it("accepts valid test result", async () => {
      const { valid } = await registry.validate("test-result", {
        pass: true,
        total: 142,
        passed: 142,
        failed: 0,
        skipped: 3,
        duration_ms: 12830,
        failures: [],
      });
      expect(valid).toBe(true);
    });

    it("rejects test result with negative total", async () => {
      const { valid } = await registry.validate("test-result", {
        pass: true,
        total: -1,
        passed: 0,
        failed: 0,
        skipped: 0,
        duration_ms: 0,
      });
      expect(valid).toBe(false);
    });
  });

  describe("adapter-output schema", () => {
    it("accepts valid adapter output", async () => {
      const { valid } = await registry.validate("adapter-output", {
        adapter_version: "1.0.0",
        source_format: "junit_xml",
        source_file: "unit.xml",
        converted_at: "2026-02-09T10:00:00Z",
        result: {
          pass: true,
          total: 50,
          passed: 50,
          failed: 0,
          skipped: 0,
          duration_ms: 5000,
          failures: [],
        },
      });
      expect(valid).toBe(true);
    });

    it("rejects adapter output with invalid source_format", async () => {
      const { valid } = await registry.validate("adapter-output", {
        adapter_version: "1.0.0",
        source_format: "unknown_format",
        source_file: "test.xml",
        converted_at: "2026-02-09T10:00:00Z",
        result: { pass: true, total: 1, passed: 1, failed: 0, skipped: 0, duration_ms: 100 },
      });
      expect(valid).toBe(false);
    });
  });

  describe("baseline schema", () => {
    it("accepts valid baseline", async () => {
      const { valid } = await registry.validate("baseline", {
        main_sha: "a1b2c3d",
        captured_at: "2026-02-09T10:00:00Z",
        tests: {
          unit: { total: 142, passed: 142, failed: 0, duration_ms: 12830 },
          integration: { total: 38, passed: 38, failed: 0, duration_ms: 45200 },
        },
        flaky_tests: ["test_flaky_1"],
      });
      expect(valid).toBe(true);
    });

    it("rejects baseline with invalid sha", async () => {
      const { valid } = await registry.validate("baseline", {
        main_sha: "INVALID_SHA",
        captured_at: "2026-02-09T10:00:00Z",
        tests: {},
      });
      expect(valid).toBe(false);
    });
  });

  describe("manifest schema", () => {
    it("accepts valid manifest", async () => {
      const { valid } = await registry.validate("manifest", {
        incubation_id: "develop-a1b2c3d-20260209-001",
        created_at: "2026-02-09T10:00:00Z",
        phase: "1a",
        id_composition: {
          branch: "develop",
          base_sha: "a1b2c3d",
          timestamp: "20260209",
          run_seq: "001",
        },
        schema_registry: {
          manifest: "1.0.0",
          freeze: "1.0.0",
          integration: "1.0.0",
          test_result: "1.0.0",
          judge_report: "1.0.0",
        },
        required_evidence: ["freeze.json", "integration.json"],
        artifacts: [
          {
            path: "freeze.json",
            schema: "freeze@1.0.0",
            sha256: "a".repeat(64),
            produced_by: "aether-freeze",
            produced_at: "2026-02-09T10:00:00Z",
          },
        ],
        manifest_signature: null,
      });
      expect(valid).toBe(true);
    });

    it("rejects manifest with invalid phase", async () => {
      const { valid } = await registry.validate("manifest", {
        incubation_id: "test",
        created_at: "2026-02-09T10:00:00Z",
        phase: "99",
        id_composition: { branch: "develop", base_sha: "a1b2c3d", timestamp: "20260209", run_seq: "001" },
        schema_registry: { manifest: "1.0.0", freeze: "1.0.0", integration: "1.0.0", test_result: "1.0.0", judge_report: "1.0.0" },
        required_evidence: [],
        artifacts: [],
      });
      expect(valid).toBe(false);
    });
  });

  describe("judge-report schema", () => {
    it("accepts valid judge report", async () => {
      const { valid } = await registry.validate("judge-report", {
        incubation_id: "develop-a1b2c3d-20260209-001",
        timestamp: "2026-02-09T12:00:00Z",
        phase: "1a",
        change_summary: {
          source_branch: "feature/foo",
          change_type: "feature",
          files_changed: ["src/foo.ts"],
          description: "Add foo feature",
        },
        risk_level: "low",
        verification: {
          lint: { pass: true, errors: 0, warnings: 0 },
          unit_tests: { pass: true, total: 100, passed: 100, failed: 0, skipped: 0, duration_ms: 5000 },
        },
        decision: "promote",
        rejection_reasons: [],
        evidence_artifact_paths: ["freeze.json", "test-results/unit.json"],
      });
      expect(valid).toBe(true);
    });

    it("rejects judge report with invalid decision", async () => {
      const { valid } = await registry.validate("judge-report", {
        incubation_id: "test",
        timestamp: "2026-02-09T12:00:00Z",
        phase: "1a",
        change_summary: {
          source_branch: "feature/foo",
          change_type: "feature",
          files_changed: [],
          description: "test",
        },
        risk_level: "low",
        verification: {
          lint: { pass: true },
          unit_tests: { pass: true, total: 1, passed: 1, failed: 0, skipped: 0, duration_ms: 100 },
        },
        decision: "maybe",
        evidence_artifact_paths: [],
      });
      expect(valid).toBe(false);
    });
  });
});
