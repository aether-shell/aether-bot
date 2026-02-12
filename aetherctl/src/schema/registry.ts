import fs from "node:fs";
import path from "node:path";
import { loadAjv, type AjvValidateFn, type AjvInstance } from "./ajv.js";

export type SchemaEntry = {
  name: string;
  version: string;
  filePath: string;
  schema: unknown;
};

/**
 * Schema registry — discovers and loads all JSON Schemas from a directory.
 * Provides compile-on-demand validation functions.
 */
export class SchemaRegistry {
  private entries = new Map<string, SchemaEntry>();
  private validators = new Map<string, AjvValidateFn>();
  private ajv: AjvInstance | null = null;

  constructor(private readonly schemaDir: string) {}

  /** Discover all *.schema.json files in the schema directory. */
  async load(): Promise<void> {
    if (!fs.existsSync(this.schemaDir)) {
      throw new Error(`Schema directory not found: ${this.schemaDir}`);
    }

    const files = fs.readdirSync(this.schemaDir).filter((f) => f.endsWith(".schema.json"));

    for (const file of files) {
      const filePath = path.join(this.schemaDir, file);
      const raw = fs.readFileSync(filePath, "utf8");
      const schema = JSON.parse(raw) as Record<string, unknown>;

      // Derive name from filename: "freeze.schema.json" → "freeze"
      const name = file.replace(/\.schema\.json$/, "");

      // Extract version from schema title or default to "1.0.0"
      const version = extractVersion(schema) ?? "1.0.0";

      this.entries.set(name, { name, version, filePath, schema });
    }

    this.ajv = await loadAjv();
  }

  /** Get a schema entry by name. */
  get(name: string): SchemaEntry | undefined {
    return this.entries.get(name);
  }

  /** List all registered schema names. */
  names(): string[] {
    return [...this.entries.keys()].sort();
  }

  /** Get the version registry map (name → version). */
  versions(): Record<string, string> {
    const result: Record<string, string> = {};
    for (const [name, entry] of this.entries) {
      result[name] = entry.version;
    }
    return result;
  }

  /** Compile and cache a validator for the given schema name. */
  async getValidator(name: string): Promise<AjvValidateFn> {
    const cached = this.validators.get(name);
    if (cached) return cached;

    const entry = this.entries.get(name);
    if (!entry) {
      throw new Error(`Schema not found: ${name}`);
    }

    if (!this.ajv) {
      this.ajv = await loadAjv();
    }

    const validate = this.ajv.compile(entry.schema);
    this.validators.set(name, validate);
    return validate;
  }

  /** Validate data against a named schema. Returns errors or null. */
  async validate(name: string, data: unknown): Promise<{ valid: boolean; errors: string | null }> {
    const validate = await this.getValidator(name);
    const valid = validate(data) as boolean;

    if (!this.ajv) {
      this.ajv = await loadAjv();
    }

    return {
      valid,
      errors: valid ? null : this.ajv.errorsText(validate.errors),
    };
  }
}

/** Extract a semver-like version from schema metadata. */
function extractVersion(schema: Record<string, unknown>): string | null {
  // Check for explicit version field
  if (typeof schema.version === "string") return schema.version;

  // Try to extract from $id (e.g., "...@1.0.0")
  if (typeof schema.$id === "string") {
    const m = /@(\d+\.\d+\.\d+)/.exec(schema.$id);
    if (m) return m[1];
  }

  return null;
}

/** Create and load a registry from the default schemas directory. */
export async function createRegistry(schemaDir?: string): Promise<SchemaRegistry> {
  const dir = schemaDir ?? path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../schemas");
  const registry = new SchemaRegistry(dir);
  await registry.load();
  return registry;
}
