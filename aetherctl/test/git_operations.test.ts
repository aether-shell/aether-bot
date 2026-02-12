import { describe, expect, it, vi } from "vitest";
import { checkMergeAllowed } from "../src/git/branch-guard.js";

describe("branch-guard", () => {
  it("allows develop → main by promoter", () => {
    const result = checkMergeAllowed("develop", "main", "promoter");
    expect(result.allowed).toBe(true);
  });

  it("rejects feature → main (must go through develop)", () => {
    const result = checkMergeAllowed("feature/foo", "main", "promoter");
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain("develop");
  });

  it("rejects develop → main by non-promoter actor", () => {
    const result = checkMergeAllowed("develop", "main", "developer");
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain("promoter");
  });

  it("allows any branch → develop by any actor", () => {
    const result = checkMergeAllowed("feature/bar", "develop", "developer");
    expect(result.allowed).toBe(true);
  });

  it("allows feature → staging by any actor", () => {
    const result = checkMergeAllowed("feature/baz", "staging", "ci");
    expect(result.allowed).toBe(true);
  });

  it("is case-insensitive for actor name", () => {
    const result = checkMergeAllowed("develop", "main", "Promoter");
    expect(result.allowed).toBe(true);
  });
});

describe("git operations (unit)", () => {
  it("GitOperations class is importable", async () => {
    const { GitOperations } = await import("../src/git/operations.js");
    expect(GitOperations).toBeDefined();
  });
});
