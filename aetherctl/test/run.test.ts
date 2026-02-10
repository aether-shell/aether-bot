import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { run, loadState, statePathForRun } from "../src/commands/run.js";

describe("run state", () => {
  it("creates state.json and marks validate ok", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "aetherctl-run-"));
    const configDir = path.join(tmp, "config");
    fs.mkdirSync(configDir, { recursive: true });
    fs.writeFileSync(
      path.join(configDir, "config.base.yaml"),
      "phase: 1a\n",
      "utf8"
    );

    const runsRoot = path.join(tmp, ".aether", "runs");

    const cwd = process.cwd();
    process.chdir(tmp);
    try {
      const res = await run({ configDir: "config", runsRoot });
      if (!res.ok) {
        // Helpful when this test fails in CI: bubble up the run() error.
        throw new Error(`run() failed: ${JSON.stringify(res.error)}`);
      }
      expect(res.ok).toBe(true);

      const sp = statePathForRun(runsRoot, res.runId);
      expect(fs.existsSync(sp)).toBe(true);

      const state = loadState(sp);
      expect(state.runId).toBe(res.runId);
      expect(state.configPath).toBe("config");
      const step = state.steps.find((s) => s.id === "validate");
      expect(step?.status).toBe("ok");
    } finally {
      process.chdir(cwd);
    }
  });

  it("resumes existing run id", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "aetherctl-run-"));
    const configDir = path.join(tmp, "config");
    fs.mkdirSync(configDir, { recursive: true });
    fs.writeFileSync(
      path.join(configDir, "config.base.yaml"),
      "phase: 1a\n",
      "utf8"
    );
    const runsRoot = path.join(tmp, ".aether", "runs");

    const cwd = process.cwd();
    process.chdir(tmp);
    try {
      const first = await run({ configDir: "config", runsRoot });
      if (!first.ok) {
        throw new Error(`first run() failed: ${JSON.stringify(first.error)}`);
      }
      expect(first.ok).toBe(true);

      const second = await run({ configDir: "config", runsRoot, runId: first.runId });
      if (!second.ok) {
        throw new Error(`second run() failed: ${JSON.stringify(second.error)}`);
      }
      expect(second.ok).toBe(true);

      expect(second.runId).toBe(first.runId);
    } finally {
      process.chdir(cwd);
    }
  });
});
