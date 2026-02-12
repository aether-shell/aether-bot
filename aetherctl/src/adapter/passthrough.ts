import fs from "node:fs";
import type { TestResult } from "../types/adapter-output.js";

/**
 * Passthrough adapter — reads a JSON file that already conforms to TestResult.
 * Validates required fields exist.
 */
export function passthroughJson(filePath: string): TestResult {
  const raw = fs.readFileSync(filePath, "utf8");
  const data = JSON.parse(raw) as TestResult;

  // Minimal validation
  if (typeof data.pass !== "boolean" || typeof data.total !== "number") {
    throw new Error(`Invalid test result JSON: missing required fields in ${filePath}`);
  }

  return {
    pass: data.pass,
    total: data.total,
    passed: data.passed ?? 0,
    failed: data.failed ?? 0,
    skipped: data.skipped ?? 0,
    duration_ms: data.duration_ms ?? 0,
    failures: data.failures ?? [],
  };
}
