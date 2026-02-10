import { describe, expect, it } from "vitest";

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { validateAll } from "../src/commands/validate.js";

async function sha256Hex(buf: Buffer): Promise<string> {
  const crypto = await import("node:crypto");
  const h = crypto.createHash("sha256");
  h.update(buf);
  return h.digest("hex");
}

describe("aetherctl validate artifacts manifest", () => {
  it("fails when manifest missing", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "aetherctl-artifacts-"));
    const res = await validateAll({ configDir: path.join(import.meta.dirname, "..", "examples"), artifactsDir: tmp });
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.errors.map((e) => e.message).join("\n")).toMatch(/Missing artifacts manifest/);
  });

  it("fails when sha256 mismatch", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "aetherctl-artifacts-"));

    const payload = Buffer.from("hello", "utf8");
    const fileRel = "out.txt";
    const fileAbs = path.join(tmp, fileRel);
    fs.writeFileSync(fileAbs, payload);

    const manifest = {
      schema_version: "0.1",
      created_at: new Date().toISOString(),
      files: [
        {
          path: fileRel,
          sha256: await sha256Hex(Buffer.from("different", "utf8")),
          bytes: payload.length
        }
      ]
    };
    fs.writeFileSync(path.join(tmp, "manifest.json"), JSON.stringify(manifest, null, 2));

    const res = await validateAll({ configDir: path.join(import.meta.dirname, "..", "examples"), artifactsDir: tmp });
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.errors.map((e) => e.message).join("\n")).toMatch(/sha256 mismatch/);
  });

  it("succeeds when sha256 matches", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "aetherctl-artifacts-"));

    const payload = Buffer.from("hello", "utf8");
    const fileRel = "out.txt";
    const fileAbs = path.join(tmp, fileRel);
    fs.writeFileSync(fileAbs, payload);

    const manifest = {
      schema_version: "0.1",
      created_at: new Date().toISOString(),
      files: [
        {
          path: fileRel,
          sha256: await sha256Hex(payload),
          bytes: payload.length
        }
      ]
    };
    fs.writeFileSync(path.join(tmp, "manifest.json"), JSON.stringify(manifest, null, 2));

    const res = await validateAll({ configDir: path.join(import.meta.dirname, "..", "examples"), artifactsDir: tmp });
    expect(res.ok).toBe(true);
  });
});
