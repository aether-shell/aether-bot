import { describe, expect, it, afterEach } from "vitest";
import path from "node:path";
import { loadConfig } from "../src/config/loader.js";
import { validateConfig } from "../src/config/validator.js";

const CONFIG_DIR = path.resolve(import.meta.dirname, "../config");

describe("config loader", () => {
  const origEnv = { ...process.env };

  afterEach(() => {
    // Restore env
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("AETHER_")) delete process.env[key];
    }
    Object.assign(process.env, origEnv);
  });

  it("loads base config with all required fields", () => {
    const config = loadConfig(undefined, CONFIG_DIR);
    expect(config.schema_version).toBe("1.0.0");
    expect(config.phase).toBe("1a");
    expect(config.artifacts_dir).toBe("artifacts");
    expect(config.state_machine?.steps).toContain("freeze");
    expect(config.state_machine?.max_concurrent).toBe(1);
  });

  it("merges env-specific config over base", () => {
    const config = loadConfig("prod-self", CONFIG_DIR);
    expect(config.artifacts_dir).toBe("artifacts/prod");
    // base fields still present
    expect(config.schema_version).toBe("1.0.0");
    expect(config.state_machine?.steps).toContain("freeze");
  });

  it("applies environment variable overrides", () => {
    process.env.AETHER_ARTIFACTS_DIR = "/tmp/override";
    const config = loadConfig(undefined, CONFIG_DIR);
    expect(config.artifacts_dir).toBe("/tmp/override");
  });

  it("env vars override env-specific yaml", () => {
    process.env.AETHER_ARTIFACTS_DIR = "/tmp/env-override";
    const config = loadConfig("prod-self", CONFIG_DIR);
    expect(config.artifacts_dir).toBe("/tmp/env-override");
  });

  it("returns base config when env yaml does not exist", () => {
    const config = loadConfig("nonexistent-env", CONFIG_DIR);
    expect(config.artifacts_dir).toBe("artifacts");
  });
});

describe("config validator", () => {
  it("validates a correct base config", async () => {
    const config = loadConfig(undefined, CONFIG_DIR);
    const { valid, errors } = await validateConfig(config);
    expect(valid).toBe(true);
    expect(errors).toBeNull();
  });

  it("rejects config missing required fields", async () => {
    const bad = { phase: "1a" } as any;
    const { valid, errors } = await validateConfig(bad);
    expect(valid).toBe(false);
    expect(errors).toContain("required");
  });

  it("rejects config with invalid phase", async () => {
    const bad = { schema_version: "1.0.0", phase: "99", artifacts_dir: "x" } as any;
    const { valid } = await validateConfig(bad);
    expect(valid).toBe(false);
  });
});
