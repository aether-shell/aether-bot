import { describe, expect, it, vi } from "vitest";

import path from "node:path";

import { validateAll } from "../src/commands/validate.js";

describe("aetherctl validate", () => {
  it("returns ok for examples config", async () => {
    const cwd = process.cwd();
    const pkgDir = path.resolve(import.meta.dirname, "..");

    try {
      process.chdir(pkgDir);
      const res = await validateAll({ configDir: "examples" });
      expect(res.ok).toBe(true);
    } finally {
      process.chdir(cwd);
    }
  });

  it("fails when config dir missing", async () => {
    const cwd = process.cwd();
    const pkgDir = path.resolve(import.meta.dirname, "..");

    try {
      process.chdir(pkgDir);
      const res = await validateAll({ configDir: "definitely-not-exist" });
      expect(res.ok).toBe(false);
      if (!res.ok) expect(res.errors.map((e) => e.message).join("\n")).toMatch(/Config directory not found/);
    } finally {
      process.chdir(cwd);
    }
  });

  it("fails when artifacts dir specified but missing", async () => {
    const cwd = process.cwd();
    const pkgDir = path.resolve(import.meta.dirname, "..");

    try {
      process.chdir(pkgDir);
      const res = await validateAll({ configDir: "examples", artifactsDir: "missing-artifacts" });
      expect(res.ok).toBe(false);
      if (!res.ok) expect(res.errors.map((e) => e.message).join("\n")).toMatch(/Artifacts directory not found/);
    } finally {
      process.chdir(cwd);
    }
  });
});
