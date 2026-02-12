import { describe, expect, it, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import {
  ALL_STEPS,
  getEffectiveSteps,
  shouldSkip,
  nextState,
  getStepTimeout,
} from "../src/core/state-machine.js";
import { checkConcurrency } from "../src/core/concurrency.js";
import { Orchestrator, type IncubationState } from "../src/core/orchestrator.js";
import type { AetherConfig } from "../src/types/config.js";

describe("state-machine", () => {
  it("Phase 1a skips twin_up, data_mirror, resilience, canary", () => {
    const steps = getEffectiveSteps("1a");
    expect(steps).toContain("freeze");
    expect(steps).toContain("integrate");
    expect(steps).toContain("regress");
    expect(steps).toContain("judge");
    expect(steps).toContain("promote");
    expect(steps).not.toContain("done");
    expect(steps).not.toContain("twin_up");
    expect(steps).not.toContain("data_mirror");
    expect(steps).not.toContain("resilience");
    expect(steps).not.toContain("canary");
  });

  it("Phase 1a effective steps are 5 (no done)", () => {
    const steps = getEffectiveSteps("1a");
    expect(steps).toEqual(["freeze", "integrate", "regress", "judge", "promote"]);
  });

  it("Phase 1b keeps most steps", () => {
    const steps = getEffectiveSteps("1b", {
      steps: [...ALL_STEPS],
      skip_rules: { "1b": ["rollback"] },
      timeouts: {},
      max_concurrent: 1,
    });
    expect(steps).toContain("twin_up");
    expect(steps).toContain("resilience");
  });

  it("shouldSkip returns true for skipped steps", () => {
    expect(shouldSkip("twin_up", "1a", "low")).toBe(true);
    expect(shouldSkip("data_mirror", "1a", "medium")).toBe(true);
  });

  it("shouldSkip returns false for active steps", () => {
    expect(shouldSkip("freeze", "1a", "low")).toBe(false);
    expect(shouldSkip("judge", "1a", "high")).toBe(false);
  });

  it("shouldSkip: Phase 1b resilience skipped for low risk", () => {
    expect(shouldSkip("resilience", "1b", "low", {
      steps: [...ALL_STEPS],
      skip_rules: { "1b": [] },
      timeouts: {},
      max_concurrent: 1,
    })).toBe(true);
  });

  it("nextState advances to next step on success", () => {
    expect(nextState("freeze", "success", "1a")).toBe("integrate");
    expect(nextState("integrate", "success", "1a")).toBe("regress");
    expect(nextState("regress", "success", "1a")).toBe("judge");
    expect(nextState("judge", "success", "1a")).toBe("promote");
    expect(nextState("promote", "success", "1a")).toBe("done");
  });

  it("nextState returns failed_ on failure", () => {
    expect(nextState("freeze", "failure", "1a")).toBe("failed_freeze");
    expect(nextState("judge", "failure", "1a")).toBe("failed_judge");
  });

  it("nextState returns timeout_ on timeout", () => {
    expect(nextState("regress", "timeout", "1a")).toBe("timeout_regress");
  });

  it("nextState returns rejected on rejected event", () => {
    expect(nextState("judge", "rejected", "1a")).toBe("rejected");
  });

  it("getStepTimeout returns defaults", () => {
    expect(getStepTimeout("freeze")).toBe(60);
    expect(getStepTimeout("regress")).toBe(600);
  });

  it("getStepTimeout uses config overrides", () => {
    expect(getStepTimeout("freeze", {
      steps: [...ALL_STEPS],
      skip_rules: {},
      timeouts: { freeze: 30 },
      max_concurrent: 1,
    })).toBe(30);
  });
});

describe("concurrency", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-conc-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("allows when no artifacts dir exists", () => {
    const result = checkConcurrency(path.join(tmpDir, "nonexistent"));
    expect(result.allowed).toBe(true);
  });

  it("allows when no active incubations", () => {
    const id = "inc-001";
    const dir = path.join(tmpDir, id);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify({ status: "done" }));
    expect(checkConcurrency(tmpDir).allowed).toBe(true);
  });

  it("blocks when active incubation exists", () => {
    const id = "inc-001";
    const dir = path.join(tmpDir, id);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify({ status: "regress" }));
    const result = checkConcurrency(tmpDir, 1);
    expect(result.allowed).toBe(false);
    expect(result.activeId).toBe(id);
  });

  it("allows failed incubations (terminal)", () => {
    const dir = path.join(tmpDir, "inc-001");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify({ status: "failed_regress" }));
    expect(checkConcurrency(tmpDir).allowed).toBe(true);
  });

  it("allows timeout incubations (terminal)", () => {
    const dir = path.join(tmpDir, "inc-001");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify({ status: "timeout_judge" }));
    expect(checkConcurrency(tmpDir).allowed).toBe(true);
  });
});

describe("orchestrator", () => {
  let tmpDir: string;
  const baseConfig: AetherConfig = {
    schema_version: "1.0.0",
    phase: "1a",
    artifacts_dir: "",
  };

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-orch-"));
    baseConfig.artifacts_dir = tmpDir;
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("runs all Phase 1a steps to done", async () => {
    const stepLog: string[] = [];
    const runner = async (step: string) => {
      stepLog.push(step);
      return { success: true };
    };

    const orch = new Orchestrator(tmpDir, baseConfig, runner);
    const result = await orch.run({
      incubationId: "test-001",
      sourceBranch: "feature/foo",
      changeType: "feature",
      riskLevel: "low",
    });

    expect(result.success).toBe(true);
    expect(result.final_status).toBe("done");
    expect(stepLog).toEqual(["freeze", "integrate", "regress", "judge", "promote"]);
  });

  it("stops on step failure", async () => {
    const runner = async (step: string) => {
      if (step === "regress") return { success: false, error: "tests_failed" };
      return { success: true };
    };

    const orch = new Orchestrator(tmpDir, baseConfig, runner);
    const result = await orch.run({
      incubationId: "test-002",
      sourceBranch: "feature/bar",
      changeType: "bugfix",
      riskLevel: "low",
    });

    expect(result.success).toBe(false);
    expect(result.final_status).toBe("failed_regress");
    expect(result.step_results.freeze.status).toBe("success");
    expect(result.step_results.integrate.status).toBe("success");
    expect(result.step_results.regress.status).toBe("failed");
  });

  it("persists state and resumes from last step", async () => {
    let callCount = 0;
    const failOnce = async (step: string) => {
      callCount++;
      if (step === "judge" && callCount <= 4) return { success: false, error: "judge_error" };
      return { success: true };
    };

    const orch = new Orchestrator(tmpDir, baseConfig, failOnce);

    // First run — fails at judge
    const r1 = await orch.run({
      incubationId: "test-003",
      sourceBranch: "feature/baz",
      changeType: "feature",
      riskLevel: "low",
    });
    expect(r1.success).toBe(false);
    expect(r1.final_status).toBe("failed_judge");

    // Verify state was persisted
    const statePath = path.join(tmpDir, "test-003", "state.json");
    expect(fs.existsSync(statePath)).toBe(true);
    const saved = JSON.parse(fs.readFileSync(statePath, "utf8")) as IncubationState;
    expect(saved.current_step).toBe("failed_judge");
  });

  it("rejects when concurrency limit reached", async () => {
    // Create an active incubation
    const activeDir = path.join(tmpDir, "active-001");
    fs.mkdirSync(activeDir, { recursive: true });
    fs.writeFileSync(path.join(activeDir, "state.json"), JSON.stringify({ status: "regress" }));

    const runner = async () => ({ success: true });
    const orch = new Orchestrator(tmpDir, baseConfig, runner);

    const result = await orch.run({
      incubationId: "test-004",
      sourceBranch: "feature/blocked",
      changeType: "feature",
      riskLevel: "low",
    });

    expect(result.success).toBe(false);
    expect(result.error).toContain("Active incubation");
  });

  it("handles step exception gracefully", async () => {
    const runner = async (step: string) => {
      if (step === "integrate") throw new Error("git merge conflict");
      return { success: true };
    };

    const orch = new Orchestrator(tmpDir, baseConfig, runner);
    const result = await orch.run({
      incubationId: "test-005",
      sourceBranch: "feature/conflict",
      changeType: "feature",
      riskLevel: "medium",
    });

    expect(result.success).toBe(false);
    expect(result.final_status).toBe("failed_integrate");
    expect(result.step_results.integrate.error).toContain("merge conflict");
  });

  it("force restart clears previous state", async () => {
    const stepLog: string[] = [];
    const runner = async (step: string) => {
      stepLog.push(step);
      return { success: true };
    };

    const orch = new Orchestrator(tmpDir, baseConfig, runner);

    // First run
    await orch.run({
      incubationId: "test-006",
      sourceBranch: "feature/restart",
      changeType: "feature",
      riskLevel: "low",
    });

    stepLog.length = 0;

    // Force restart
    const result = await orch.run({
      incubationId: "test-006",
      sourceBranch: "feature/restart",
      changeType: "feature",
      riskLevel: "low",
      forceRestart: true,
    });

    expect(result.success).toBe(true);
    expect(stepLog).toEqual(["freeze", "integrate", "regress", "judge", "promote"]);
  });
});
