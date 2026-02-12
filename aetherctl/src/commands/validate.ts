import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { loadAjv } from "../schema/ajv.js";
import YAML from "yaml";

export type Diagnostic = {
  level: "error" | "warn" | "info";
  code: string;
  message: string;
  path?: string;
  details?: Record<string, unknown>;
};

export type ValidateResult = { ok: true } | { ok: false; errors: Diagnostic[] };

function diag(
  level: Diagnostic["level"],
  code: string,
  message: string,
  extra?: Pick<Diagnostic, "path" | "details">
): Diagnostic {
  return { level, code, message, ...extra };
}

function readYamlFile(filePath: string): unknown {
  const raw = fs.readFileSync(filePath, "utf8");
  return YAML.parse(raw);
}

function listYamlFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  return entries
    .filter((e) => e.isFile() && (e.name.endsWith(".yml") || e.name.endsWith(".yaml")))
    .map((e) => path.join(dir, e.name));
}

type ManifestFile = { path: string; sha256: string; bytes: number };
type ArtifactsManifest = { schema_version: string; created_at: string; files: ManifestFile[] };

function sha256FileHex(filePath: string): string {
  const hash = crypto.createHash("sha256");
  const buf = fs.readFileSync(filePath);
  hash.update(buf);
  return hash.digest("hex");
}

function isWithinDir(rootDir: string, candidatePath: string): boolean {
  const rel = path.relative(rootDir, candidatePath);
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
}

export async function validateAll(opts: {
  configDir: string;
  artifactsDir?: string;
  schemaDir?: string;
}): Promise<ValidateResult> {
  const errors: Diagnostic[] = [];

  const configDir = path.resolve(opts.configDir);
  const schemaDir = opts.schemaDir
    ? path.resolve(opts.schemaDir)
    : path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../schemas");

  if (!fs.existsSync(configDir)) {
    return { ok: false, errors: [diag("error", "CONFIG_DIR_MISSING", `Config directory not found: ${configDir}`)] };
  }
  if (!fs.existsSync(schemaDir)) {
    return { ok: false, errors: [diag("error", "SCHEMA_DIR_MISSING", `Schema directory not found: ${schemaDir}`)] };
  }

  const ajv = await loadAjv();

  const configSchemaPath = path.join(schemaDir, "config.schema.json");
  if (!fs.existsSync(configSchemaPath)) {
    return {
      ok: false,
      errors: [diag("error", "SCHEMA_MISSING", `Missing schema: ${configSchemaPath}`, { path: configSchemaPath })]
    };
  }

  const configSchema = JSON.parse(fs.readFileSync(configSchemaPath, "utf8"));
  const validateConfig = ajv.compile(configSchema);

  const files = listYamlFiles(configDir);
  if (files.length === 0) {
    errors.push(diag("error", "CONFIG_NO_YAML", `No YAML files found in config dir: ${configDir}`, { path: configDir }));
  }

  for (const file of files) {
    try {
      const doc = readYamlFile(file);
      const ok = validateConfig(doc);
      if (!ok) {
        errors.push(
          diag(
            "error",
            "CONFIG_INVALID",
            `Config invalid (${path.relative(process.cwd(), file)}): ${ajv.errorsText(validateConfig.errors)}`,
            { path: file }
          )
        );
      }
    } catch (e) {
      errors.push(
        diag(
          "error",
          "CONFIG_READ_FAILED",
          `Failed to read config (${path.relative(process.cwd(), file)}): ${(e as Error).message}`,
          { path: file }
        )
      );
    }
  }

  if (opts.artifactsDir) {
    const artifactsDir = path.resolve(opts.artifactsDir);
    if (!fs.existsSync(artifactsDir)) {
      errors.push(diag("error", "ARTIFACTS_DIR_MISSING", `Artifacts directory not found: ${artifactsDir}`, { path: artifactsDir }));
    } else {
      const manifestPath = path.join(artifactsDir, "manifest.json");
      if (!fs.existsSync(manifestPath)) {
        errors.push(
          diag("error", "ARTIFACTS_MANIFEST_MISSING", `Missing artifacts manifest: ${manifestPath}`, { path: manifestPath })
        );
      } else {
        let manifest: ArtifactsManifest | undefined;
        try {
          manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8")) as ArtifactsManifest;
        } catch (e) {
          errors.push(
            diag(
              "error",
              "ARTIFACTS_MANIFEST_JSON_INVALID",
              `Invalid JSON manifest (${path.relative(process.cwd(), manifestPath)}): ${(e as Error).message}`,
              { path: manifestPath }
            )
          );
        }

        if (manifest) {
          const manifestSchemaPath = path.join(schemaDir, "artifacts.manifest.schema.json");
          if (!fs.existsSync(manifestSchemaPath)) {
            errors.push(
              diag("error", "SCHEMA_MISSING", `Missing schema: ${manifestSchemaPath}`, { path: manifestSchemaPath })
            );
          } else {
            try {
              const manifestSchema = JSON.parse(fs.readFileSync(manifestSchemaPath, "utf8"));
              const validateManifest = ajv.compile(manifestSchema);
              const ok = validateManifest(manifest);
              if (!ok) {
                errors.push(
                  diag(
                    "error",
                    "ARTIFACTS_MANIFEST_INVALID",
                    `Artifacts manifest invalid (${path.relative(process.cwd(), manifestPath)}): ${ajv.errorsText(validateManifest.errors)}`,
                    { path: manifestPath }
                  )
                );
              }
            } catch (e) {
              errors.push(
                diag(
                  "error",
                  "ARTIFACTS_MANIFEST_VALIDATE_FAILED",
                  `Failed to validate manifest (${path.relative(process.cwd(), manifestPath)}): ${(e as Error).message}`,
                  { path: manifestPath }
                )
              );
            }
          }

          if (manifest.files && Array.isArray(manifest.files)) {
            for (const entry of manifest.files) {
              const target = path.resolve(artifactsDir, entry.path);
              if (!isWithinDir(artifactsDir, target)) {
                errors.push(
                  diag(
                    "error",
                    "ARTIFACTS_PATH_ESCAPES_DIR",
                    `Manifest path escapes artifacts dir: ${entry.path}`,
                    { path: entry.path }
                  )
                );
                continue;
              }
              if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
                errors.push(
                  diag(
                    "error",
                    "ARTIFACTS_FILE_MISSING",
                    `Missing artifact file: ${path.relative(process.cwd(), target)}`,
                    { path: target }
                  )
                );
                continue;
              }

              const st = fs.statSync(target);
              if (typeof entry.bytes === "number" && st.size !== entry.bytes) {
                errors.push(
                  diag(
                    "error",
                    "ARTIFACTS_SIZE_MISMATCH",
                    `Artifact size mismatch (${entry.path}): manifest=${entry.bytes} actual=${st.size}`,
                    {
                      path: target,
                      details: { expectedBytes: entry.bytes, actualBytes: st.size }
                    }
                  )
                );
              }

              const actualHash = sha256FileHex(target);
              if (actualHash !== entry.sha256) {
                errors.push(
                  diag(
                    "error",
                    "ARTIFACTS_SHA256_MISMATCH",
                    `Artifact sha256 mismatch (${entry.path}): manifest=${entry.sha256} actual=${actualHash}`,
                    {
                      path: target,
                      details: { expectedSha256: entry.sha256, actualSha256: actualHash }
                    }
                  )
                );
              }
            }
          }
        }
      }
    }
  }

  if (errors.length > 0) return { ok: false, errors };
  return { ok: true };
}
