import { describe, expect, it, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { Orchestrator, type IncubationState } from "../src/core/orchestrator.js";
import { generateIncubationId } from "../src/core/incubation-id.js";
import { status } from "../src/commands/status.js";
import { listArtifacts } from "../src/commands/artifacts.js";
import type { AetherConfig } from "../src/types/config.js";

describe("integration: end-to-end incubation flow", () => {
  let tmpDir: string;
  const config: AetherConfig = {
    schema_version: "1.0.0",
    phase: "1a",
    artifacts_dir: "",
  };

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-e2e-"));
    config.artifacts_dir = tmpDir;
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("full pipeline: freeze → integrate → regress → judge → promote → done", async () => {
    const stepResults: Record<string, boolean> = {};
    const runner = async (step: string) => {
      stepResults[step] = true;
      return { success: true };
    };

    const incubationId = generateIncubationId("feature/e2e-test", "abc1234", tmpDir);
    const orch = new Orchestrator(tmpDir, config, runner);
    const result = await orch.run({
      incubationId,
      sourceBranch: "feature/e2e-test",
      changeType: "feature",
      riskLevel: "low",
    });

    // Verify pipeline completed
    expect(result.success).toBe(true);
    expect(result.final_status).toBe("done");

    // Verify all Phase 1a steps executed
    expect(stepResults.freeze).toBe(true);
    expect(stepResults.integrate).toBe(true);
    expect(stepResults.regress).toBe(true);
    expect(stepResults.judge).toBe(true);
    expect(stepResults.promote).toBe(true);

    // Verify state.json persisted
    const st = status({ artifactsDir: tmpDir, incubationId });
    expect(st.ok).toBe(true);
    if (st.ok) {
      expect(st.state.current_step).toBe("done");
      expect(st.state.phase).toBe("1a");
    }

    // Verify artifacts directory exists
    const arts = listArtifacts({ artifactsDir: tmpDir, incubationId });
    expect(arts.ok).toBe(true);
    if (arts.ok) {
      expect(arts.files.some((f) => f.path === "state.json")).toBe(true);
    }
  });

  it("judge rejection produces exit code 2 scenario", async () => {
    const runner = async (step: string) => {
      if (step === "judge") return { success: false, error: "REJECTED" };
      return { success: true };
    };

    const incubationId = generateIncubationId("feature/rejected", "def5678", tmpDir);
    const orch = new Orchestrator(tmpDir, config, runner);
    const result = await orch.run({
      incubationId,
      sourceBranch: "feature/rejected",
      changeType: "feature",
      riskLevel: "medium",
    });

    expect(result.success).toBe(false);
    expect(result.final_status).toBe("rejected");

    const st = status({ artifactsDir: tmpDir, incubationId });
    expect(st.ok).toBe(true);
    if (st.ok) {
      expect(st.state.step_results.judge.status).toBe("rejected");
    }
  });
});

describe("integration: resume flow (断点续跑)", () => {
  let tmpDir: string;
  const config: AetherConfig = {
    schema_version: "1.0.0",
    phase: "1a",
    artifacts_dir: "",
  };

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-resume-"));
    config.artifacts_dir = tmpDir;
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("resumes from failed step after fix", async () => {
    let regressCallCount = 0;
    const runner = async (step: string) => {
      if (step === "regress") {
        regressCallCount++;
        if (regressCallCount === 1) return { success: false, error: "tests_failed" };
      }
      return { success: true };
    };

    const incubationId = "resume-test-001";
    const orch = new Orchestrator(tmpDir, config, runner);

    // First run — fails at regress
    const r1 = await orch.run({
      incubationId,
      sourceBranch: "feature/resume",
      changeType: "bugfix",
      riskLevel: "low",
    });

    expect(r1.success).toBe(false);
    expect(r1.final_status).toBe("failed_regress");
    expect(r1.step_results.freeze.status).toBe("success");
    expect(r1.step_results.integrate.status).toBe("success");
    expect(r1.step_results.regress.status).toBe("failed");

    // Verify state persisted at failed_regress
    const st1 = status({ artifactsDir: tmpDir, incubationId });
    expect(st1.ok).toBe(true);
    if (st1.ok) {
      expect(st1.state.current_step).toBe("failed_regress");
    }

    // "Fix" the issue and force restart — since failed_regress is terminal,
    // we need forceRestart to re-run from scratch.
    // In a real scenario, the user fixes the code and re-runs.
    const r2 = await orch.run({
      incubationId,
      sourceBranch: "feature/resume",
      changeType: "bugfix",
      riskLevel: "low",
      forceRestart: true,
    });

    // Second run succeeds (regressCallCount is now 2, so it passes)
    expect(r2.success).toBe(true);
    expect(r2.final_status).toBe("done");
    expect(r2.step_results.regress.status).toBe("success");
  });

  it("concurrent incubation is blocked", async () => {
    const slowRunner = async (step: string) => {
      return { success: true };
    };

    const orch = new Orchestrator(tmpDir, config, slowRunner);

    // Create an active incubation manually
    const activeDir = path.join(tmpDir, "active-inc");
    fs.mkdirSync(activeDir, { recursive: true });
    fs.writeFileSync(path.join(activeDir, "state.json"), JSON.stringify({
      current_step: "regress",
      updated_at: new Date().toISOString(),
    }));

    // Try to start another
    const result = await orch.run({
      incubationId: "blocked-inc",
      sourceBranch: "feature/blocked",
      changeType: "feature",
      riskLevel: "low",
    });

    expect(result.success).toBe(false);
    expect(result.error).toContain("Active incubation");
  });
});
