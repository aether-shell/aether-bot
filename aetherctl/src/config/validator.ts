import { loadAjv } from "../schema/ajv.js";
import type { AetherConfig } from "../types/config.js";

/** Minimal config schema for validation — ensures required fields exist. */
const CONFIG_SCHEMA = {
  type: "object",
  required: ["schema_version", "phase", "artifacts_dir"],
  properties: {
    schema_version: { type: "string", minLength: 1 },
    phase: { type: "string", enum: ["1a", "1b", "2", "3"] },
    artifacts_dir: { type: "string", minLength: 1 },
    required_evidence: { type: "array", items: { type: "string" } },
    state_machine: {
      type: "object",
      required: ["steps", "max_concurrent"],
      properties: {
        steps: { type: "array", items: { type: "string" }, minItems: 1 },
        skip_rules: { type: "object" },
        timeouts: { type: "object" },
        max_concurrent: { type: "integer", minimum: 1 },
      },
    },
    thresholds: { type: "object" },
    risk_policy: {
      type: "object",
      properties: {
        path_rules: {
          type: "array",
          items: {
            type: "object",
            required: ["pattern", "level"],
            properties: {
              pattern: { type: "string" },
              level: { type: "string", enum: ["low", "medium", "high"] },
            },
          },
        },
        auto_escalation: { type: "array", items: { type: "string" } },
      },
    },
  },
};

export type ConfigValidationResult = {
  valid: boolean;
  errors: string | null;
};

/** Validate a loaded config against the config schema. */
export async function validateConfig(config: AetherConfig): Promise<ConfigValidationResult> {
  const ajv = await loadAjv();
  const validate = ajv.compile(CONFIG_SCHEMA);
  const valid = validate(config) as boolean;
  return {
    valid,
    errors: valid ? null : ajv.errorsText(validate.errors),
  };
}
