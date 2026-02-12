import fs from "node:fs";
import path from "node:path";
import YAML from "yaml";
import type { AetherConfig } from "../types/config.js";

const CONFIG_DIR = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../config");

/**
 * Deep merge two objects. `override` values take precedence.
 * Arrays are replaced, not concatenated.
 */
function deepMerge<T extends Record<string, unknown>>(base: T, override: Partial<T>): T {
  const result = { ...base };
  for (const key of Object.keys(override) as (keyof T)[]) {
    const val = override[key];
    if (val !== undefined && val !== null && typeof val === "object" && !Array.isArray(val)) {
      result[key] = deepMerge(
        (result[key] ?? {}) as Record<string, unknown>,
        val as Record<string, unknown>,
      ) as T[keyof T];
    } else if (val !== undefined) {
      result[key] = val as T[keyof T];
    }
  }
  return result;
}

/** Load a YAML file and return parsed object, or empty object if not found. */
function loadYaml(filePath: string): Record<string, unknown> {
  if (!fs.existsSync(filePath)) return {};
  const raw = fs.readFileSync(filePath, "utf8");
  return (YAML.parse(raw) as Record<string, unknown>) ?? {};
}

/** Apply AETHER_ prefixed environment variable overrides. */
function applyEnvOverrides(config: Record<string, unknown>): Record<string, unknown> {
  const prefix = "AETHER_";
  for (const [key, value] of Object.entries(process.env)) {
    if (!key.startsWith(prefix) || value === undefined) continue;
    // AETHER_ARTIFACTS_DIR → artifacts_dir
    const configKey = key.slice(prefix.length).toLowerCase();
    config[configKey] = value;
  }
  return config;
}

/**
 * Load layered config: base.yaml ← env.yaml ← environment variables.
 *
 * @param envName - Optional environment name (e.g., "prod-self", "regression-twin").
 *                  Loads `config/{envName}.yaml` as override layer.
 * @param configDir - Optional config directory path override.
 */
export function loadConfig(envName?: string, configDir?: string): AetherConfig {
  const dir = configDir ?? CONFIG_DIR;

  // Layer 1: base.yaml
  const base = loadYaml(path.join(dir, "base.yaml"));

  // Layer 2: environment-specific override
  let merged = base;
  if (envName) {
    const envConfig = loadYaml(path.join(dir, `${envName}.yaml`));
    merged = deepMerge(base, envConfig);
  }

  // Layer 3: environment variables
  merged = applyEnvOverrides(merged);

  return merged as unknown as AetherConfig;
}
