import { describe, expect, it, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { generateIncubationId } from "../src/core/incubation-id.js";
import { status, listIncubations } from "../src/commands/status.js";
import { listArtifacts } from "../src/commands/artifacts.js";
import { readBaseline, updateBaseline } from "../src/commands/baseline.js";
import { EXIT } from "../src/commands/exit-codes.js";

describe("exit-codes", () => {
  it("defines all required exit codes", () => {
    expect(EXIT.SUCCESS).toBe(0);
    expect(EXIT.INCUBATION_FAILED).toBe(1);
    expect(EXIT.JUDGE_REJECTED).toBe(2);
    expect(EXIT.INVALID_ARGS).toBe(3);
    expect(EXIT.CONCURRENT_CONFLICT).toBe(4);
  });
});

describe("incubation-id", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-id-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("generates valid format", () => {
    const id = generateIncubationId("feature/foo", "abc1234def5678", tmpDir);
    expect(id).toMatch(/^feature-foo-abc1234-\d{8}-001$/);
  });

  it("increments seq for same prefix", () => {
    const id1 = generateIncubationId("develop", "abc1234def", tmpDir);
    // Create directory for first id
    fs.mkdirSync(path.join(tmpDir, id1), { recursive: true });
    const id2 = generateIncubationId("develop", "abc1234def", tmpDir);
    expect(id2).toMatch(/-002$/);
  });

  it("sanitizes branch name", () => {
    const id = generateIncubationId("feature/special@chars!", "abc1234", tmpDir);
    expect(id).not.toContain("@");
    expect(id).not.toContain("!");
  });
});

describe("status command", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-status-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns error for missing incubation", () => {
    const res = status({ artifactsDir: tmpDir, incubationId: "nonexistent" });
    expect(res.ok).toBe(false);
  });

  it("reads existing state", () => {
    const id = "test-001";
    const dir = path.join(tmpDir, id);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify({
      incubation_id: id,
      phase: "1a",
      current_step: "regress",
      source_branch: "feature/foo",
      updated_at: "2026-02-11T00:00:00Z",
    }));

    const res = status({ artifactsDir: tmpDir, incubationId: id });
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.state.current_step).toBe("regress");
    }
  });

  it("lists all incubations", () => {
    for (const [id, step] of [["inc-001", "done"], ["inc-002", "regress"]]) {
      const dir = path.join(tmpDir, id);
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify({
        current_step: step,
        updated_at: "2026-02-11T00:00:00Z",
      }));
    }

    const list = listIncubations(tmpDir);
    expect(list).toHaveLength(2);
    expect(list.map((l) => l.id).sort()).toEqual(["inc-001", "inc-002"]);
  });

  it("returns empty list for missing dir", () => {
    expect(listIncubations(path.join(tmpDir, "nope"))).toEqual([]);
  });
});

describe("artifacts command", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-art-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns error for missing incubation", () => {
    const res = listArtifacts({ artifactsDir: tmpDir, incubationId: "nope" });
    expect(res.ok).toBe(false);
  });

  it("lists files recursively", () => {
    const id = "test-001";
    const dir = path.join(tmpDir, id);
    fs.mkdirSync(path.join(dir, "test-results"), { recursive: true });
    fs.writeFileSync(path.join(dir, "state.json"), "{}");
    fs.writeFileSync(path.join(dir, "test-results", "unit.json"), "{}");

    const res = listArtifacts({ artifactsDir: tmpDir, incubationId: id });
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.files.length).toBe(2);
      expect(res.files.map((f) => f.path).sort()).toEqual(["state.json", "test-results/unit.json"]);
    }
  });
});

describe("baseline command", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aether-bl-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns error when no baseline exists", () => {
    const res = readBaseline(tmpDir);
    expect(res.ok).toBe(false);
  });

  it("creates and reads baseline", () => {
    const update = updateBaseline(tmpDir, "abc1234", { total: 100, passed: 100, failed: 0, duration_ms: 5000 });
    expect(update.ok).toBe(true);

    const read = readBaseline(tmpDir);
    expect(read.ok).toBe(true);
    if (read.ok) {
      expect(read.baseline.main_sha).toBe("abc1234");
      expect(read.baseline.tests.unit?.total).toBe(100);
    }
  });
});
