import { describe, expect, it } from "vitest";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ensureState, computePlanDigest, updateState, loadState } from "../src/openspec/state.js";

describe("openspec taskmode state", () => {
  it("writes state.json atomically and computes plan digest", async () => {
    const repoPath = await mkdtemp(join(tmpdir(), "aether-taskmode-"));
    await mkdir(join(repoPath, "openspec", "changes", "c1"), { recursive: true });
    await writeFile(join(repoPath, "openspec", "config.yaml"), "x: 1\n", "utf8");
    await writeFile(join(repoPath, "openspec", "changes", "c1", "proposal.md"), "proposal\n", "utf8");
    await writeFile(join(repoPath, "openspec", "changes", "c1", "design.md"), "design\n", "utf8");
    await writeFile(join(repoPath, "openspec", "changes", "c1", "tasks.md"), "- [ ] t\n", "utf8");

    await ensureState(repoPath, "c1");
    const d1 = await computePlanDigest(repoPath, "c1");

    await updateState(repoPath, "c1", (s) => ({ ...s, status: "WAIT_APPROVAL", plan: { ...s.plan, planDigest: d1 } }));
    const st = await loadState(repoPath, "c1");

    expect(st.status).toBe("WAIT_APPROVAL");
    expect(st.plan.planDigest).toBeTypeOf("string");
    expect(st.plan.planDigest?.length).toBeGreaterThan(10);
  });
});
