import { describe, expect, it, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import {
  checkManifestExists,
  checkRequiredArtifacts,
  checkIntegrity,
  checkSchemaValidity,
  checkDataConsistency,
  checkSignatureValid,
  checkSignatureExists,
  runFailClosedChecks,
} from "../src/judge/fail-closed.js";
import { getRequiredEvidence, requiresSignature } from "../src/judge/required-evidence.js";
import { checkThresholds } from "../src/judge/threshold-checker.js";
import { BaselineManager } from "../src/judge/baseline-manager.js";
import { judge } from "../src/judge/judge.js";
import { createRegistry } from "../src/schema/registry.js";
import type { Manifest } from "../src/types/manifest.js";

const SCHEMA_DIR = path.resolve(import.meta.dirname, "../schemas");

const VALID_MANIFEST: Manifest = {
  incubation_id: "develop-abc1234-20260209-001",
  created_at: "2026-02-09T10:00:00Z",
  phase: "1a",
  id_composition: { branch: "develop", base_sha: "abc1234", timestamp: "20260209", run_seq: "001" },
  schema_registry: { manifest: "1.0.0", freeze: "1.0.0", integration: "1.0.0", test_result: "1.0.0", judge_report: "1.0.0" },
  required_evidence: ["freeze.json", "integration.json", "test-results/lint.json", "test-results/unit.json"],
  artifacts: [
    { path: "freeze.json", schema: "freeze@1.0.0", sha256: "aaa", produced_by: "aether-freeze", produced_at: "2026-02-09T10:00:00Z" },
    { path: "integration.json", schema: "integration@1.0.0", sha256: "bbb", produced_by: "aether-integrate", produced_at: "2026-02-09T10:00:00Z" },
    { path: "test-results/lint.json", schema: "test-result@1.0.0", sha256: "ccc", produced_by: "aether-adapter", produced_at: "2026-02-09T10:00:00Z" },
    { path: "test-results/unit.json", schema: "test-result@1.0.0", sha256: "ddd", produced_by: "aether-adapter", produced_at: "2026-02-09T10:00:00Z" },
  ],
  manifest_signature: null,
};

describe("fail-closed rules (§6.10)", () => {
  it("Rule 1: rejects when manifest is missing", () => {
    const v = checkManifestExists(null);
    expect(v).not.toBeNull();
    expect(v!.rule).toBe(1);
    expect(v!.reason).toBe("missing_manifest");
  });

  it("Rule 1: passes when manifest exists", () => {
    const v = checkManifestExists(VALID_MANIFEST);
    expect(v).toBeNull();
  });

  it("Rule 2: rejects when required artifact is missing", () => {
    const violations = checkRequiredArtifacts(
      ["freeze.json", "missing.json"],
      VALID_MANIFEST.artifacts,
    );
    expect(violations).toHaveLength(1);
    expect(violations[0].rule).toBe(2);
    expect(violations[0].reason).toContain("missing.json");
  });

  it("Rule 2: passes when all required artifacts present", () => {
    const violations = checkRequiredArtifacts(
      ["freeze.json", "integration.json"],
      VALID_MANIFEST.artifacts,
    );
    expect(violations).toHaveLength(0);
  });

  it("Rule 3: rejects when sha256 mismatch", () => {
    const checksums = new Map([["freeze.json", "wrong_hash"]]);
    const violations = checkIntegrity(VALID_MANIFEST.artifacts, checksums);
    expect(violations).toHaveLength(1);
    expect(violations[0].rule).toBe(3);
    expect(violations[0].reason).toContain("integrity_mismatch");
  });

  it("Rule 3: passes when checksums match", () => {
    const checksums = new Map([["freeze.json", "aaa"]]);
    const violations = checkIntegrity(VALID_MANIFEST.artifacts, checksums);
    expect(violations).toHaveLength(0);
  });

  it("Rule 4: rejects when schema validation fails", () => {
    const results = new Map([["freeze.json", { valid: false, path: "freeze.json" }]]);
    const violations = checkSchemaValidity(results);
    expect(violations).toHaveLength(1);
    expect(violations[0].rule).toBe(4);
  });

  it("Rule 5: rejects contradictory evidence (total < passed + failed)", () => {
    const violations = checkDataConsistency([
      { path: "test-results/unit.json", total: 5, passed: 3, failed: 4, skipped: 0 },
    ]);
    expect(violations).toHaveLength(1);
    expect(violations[0].rule).toBe(5);
    expect(violations[0].reason).toContain("contradictory_evidence");
  });

  it("Rule 5: passes consistent data", () => {
    const violations = checkDataConsistency([
      { path: "test-results/unit.json", total: 10, passed: 8, failed: 2, skipped: 0 },
    ]);
    expect(violations).toHaveLength(0);
  });

  it("Rule 6: skipped in Phase 1a", () => {
    const v = checkSignatureValid("1a", false);
    expect(v).toBeNull();
  });

  it("Rule 6: rejects invalid signature in Phase 1b", () => {
    const v = checkSignatureValid("1b", false);
    expect(v).not.toBeNull();
    expect(v!.rule).toBe(6);
  });

  it("Rule 7: skipped in Phase 1a", () => {
    const v = checkSignatureExists("1a", null);
    expect(v).toBeNull();
  });

  it("Rule 7: rejects missing signature in Phase 1b", () => {
    const v = checkSignatureExists("1b", null);
    expect(v).not.toBeNull();
    expect(v!.rule).toBe(7);
  });

  it("runFailClosedChecks returns early on missing manifest", () => {
    const violations = runFailClosedChecks({
      manifest: null,
      requiredEvidence: [],
      actualChecksums: new Map(),
      schemaResults: new Map(),
      testResults: [],
      signatureValid: null,
    });
    expect(violations).toHaveLength(1);
    expect(violations[0].rule).toBe(1);
  });
});

describe("required-evidence (§6.9)", () => {
  it("Phase 1a requires freeze, integration, lint, unit", () => {
    const evidence = getRequiredEvidence("1a");
    expect(evidence).toContain("freeze.json");
    expect(evidence).toContain("test-results/unit.json");
    expect(evidence).not.toContain("test-results/e2e.json");
  });

  it("Phase 1b requires e2e", () => {
    const evidence = getRequiredEvidence("1b");
    expect(evidence).toContain("test-results/e2e.json");
  });

  it("Phase 1b medium risk requires resilience", () => {
    const evidence = getRequiredEvidence("1b", "medium");
    expect(evidence).toContain("resilience/*.json");
  });

  it("Phase 1a does not require signature", () => {
    expect(requiresSignature("1a")).toBe(false);
  });

  it("Phase 1b requires signature", () => {
    expect(requiresSignature("1b")).toBe(true);
  });
});

describe("threshold-checker (§7.1)", () => {
  it("passes when candidate matches baseline", () => {
    const result = checkThresholds({
      baseline: { total: 100, passed: 100, failed: 0, duration_ms: 5000 },
      candidate: { total: 100, passed: 100, failed: 0, duration_ms: 5000 },
      change_type: "feature",
    });
    expect(result.pass).toBe(true);
    expect(result.violations).toHaveLength(0);
  });

  it("rejects when pass rate drops", () => {
    const result = checkThresholds({
      baseline: { total: 100, passed: 100, failed: 0, duration_ms: 5000 },
      candidate: { total: 100, passed: 90, failed: 10, duration_ms: 5000 },
      change_type: "feature",
    });
    expect(result.pass).toBe(false);
    expect(result.violations.some((v) => v.metric === "functionality_delta_pct")).toBe(true);
  });

  it("rejects when fail rate increases", () => {
    const result = checkThresholds({
      baseline: { total: 100, passed: 100, failed: 0, duration_ms: 5000 },
      candidate: { total: 100, passed: 99, failed: 1, duration_ms: 5000 },
      change_type: "bugfix",
    });
    expect(result.pass).toBe(false);
    expect(result.violations.some((v) => v.metric === "stability_delta_pct")).toBe(true);
  });

  it("rejects when latency exceeds threshold", () => {
    const result = checkThresholds({
      baseline: { total: 100, passed: 100, failed: 0, duration_ms: 5000 },
      candidate: { total: 100, passed: 100, failed: 0, duration_ms: 6000 },
      change_type: "feature",
    });
    expect(result.pass).toBe(false);
    expect(result.violations.some((v) => v.metric === "p95_latency_delta_pct")).toBe(true);
  });

  it("uses custom thresholds from config", () => {
    const result = checkThresholds(
      {
        baseline: { total: 100, passed: 100, failed: 0, duration_ms: 5000 },
        candidate: { total: 100, passed: 100, failed: 0, duration_ms: 10000 },
        change_type: "dependency",
      },
      { dependency: { functionality_min_pct: 0, stability_max_pct: 0, p95_latency_max_pct: 200 } },
    );
    expect(result.pass).toBe(true);
  });
});

describe("baseline-manager", () => {
  let tmpDir: string;
  let manager: BaselineManager;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-baseline-"));
    manager = new BaselineManager(tmpDir);
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns null when no baseline exists", () => {
    expect(manager.read()).toBeNull();
  });

  it("writes and reads baseline", () => {
    const baseline = manager.createFromResults(
      "abc1234",
      { total: 100, passed: 100, failed: 0, duration_ms: 5000 },
    );
    manager.write(baseline);
    const read = manager.read();
    expect(read).not.toBeNull();
    expect(read!.main_sha).toBe("abc1234");
    expect(read!.tests.unit?.total).toBe(100);
  });

  it("marks flaky tests", () => {
    const baseline = manager.createFromResults("abc1234");
    manager.write(baseline);
    manager.markFlaky("test_flaky_1");
    const read = manager.read();
    expect(read!.flaky_tests).toContain("test_flaky_1");
  });
});

describe("judge main flow", () => {
  const baseVerification = {
    lint: { pass: true, errors: 0, warnings: 0 },
    unit_tests: { pass: true, total: 100, passed: 100, failed: 0, skipped: 0, duration_ms: 5000 },
  };

  const baseInput = {
    manifest: VALID_MANIFEST,
    actualChecksums: new Map([
      ["freeze.json", "aaa"],
      ["integration.json", "bbb"],
      ["test-results/lint.json", "ccc"],
      ["test-results/unit.json", "ddd"],
    ]),
    schemaResults: new Map<string, { valid: boolean; path: string }>(),
    testResults: [{ path: "test-results/unit.json", total: 100, passed: 100, failed: 0, skipped: 0 }],
    signatureValid: null,
    verification: baseVerification,
    changeSummary: {
      source_branch: "feature/foo",
      change_type: "feature" as const,
      files_changed: ["src/foo.ts"],
      description: "Add foo",
    },
    riskLevel: "low" as const,
    baseline: null,
    evidenceArtifactPaths: ["freeze.json", "test-results/unit.json"],
  };

  it("promotes when all checks pass (no baseline)", () => {
    const { report, violations } = judge(baseInput);
    expect(violations).toHaveLength(0);
    expect(report.decision).toBe("promote");
  });

  it("rejects when manifest is missing", () => {
    const { report } = judge({ ...baseInput, manifest: null });
    expect(report.decision).toBe("reject");
    expect(report.rejection_reasons).toContain("missing_manifest");
  });

  it("rejects when unit tests fail", () => {
    const { report } = judge({
      ...baseInput,
      verification: {
        ...baseVerification,
        unit_tests: { pass: false, total: 100, passed: 90, failed: 10, skipped: 0, duration_ms: 5000 },
      },
    });
    expect(report.decision).toBe("reject");
    expect(report.rejection_reasons).toContain("unit_tests_failed");
  });

  it("rejects when lint fails", () => {
    const { report } = judge({
      ...baseInput,
      verification: {
        ...baseVerification,
        lint: { pass: false, errors: 3, warnings: 0 },
      },
    });
    expect(report.decision).toBe("reject");
    expect(report.rejection_reasons).toContain("lint_failed");
  });

  it("rejects on threshold violation with baseline", () => {
    const { report } = judge({
      ...baseInput,
      baseline: {
        main_sha: "abc1234",
        captured_at: "2026-02-09T10:00:00Z",
        tests: { unit: { total: 100, passed: 100, failed: 0, duration_ms: 5000 } },
      },
      verification: {
        ...baseVerification,
        unit_tests: { pass: true, total: 100, passed: 90, failed: 10, skipped: 0, duration_ms: 5000 },
      },
    });
    expect(report.decision).toBe("reject");
    expect(report.rejection_reasons?.some((r) => r.includes("threshold_violation"))).toBe(true);
  });

  it("judge report passes schema validation", async () => {
    const registry = await createRegistry(SCHEMA_DIR);
    const { report } = judge(baseInput);
    const { valid, errors } = await registry.validate("judge-report", report);
    expect(errors).toBeNull();
    expect(valid).toBe(true);
  });
});
