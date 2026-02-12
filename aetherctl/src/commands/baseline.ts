import fs from "node:fs";
import path from "node:path";
import { BaselineManager } from "../judge/baseline-manager.js";
import type { Baseline } from "../types/baseline.js";

export type BaselineResult =
  | { ok: true; baseline: Baseline }
  | { ok: false; error: string };

/**
 * Read current baseline.
 */
export function readBaseline(artifactsDir: string): BaselineResult {
  const manager = new BaselineManager(artifactsDir);
  const baseline = manager.read();
  if (!baseline) {
    return { ok: false, error: "No baseline found. Run a successful incubation first." };
  }
  return { ok: true, baseline };
}

/**
 * Update baseline from a specific incubation's results.
 */
export function updateBaseline(
  artifactsDir: string,
  mainSha: string,
  unit?: { total: number; passed: number; failed: number; duration_ms: number },
): BaselineResult {
  const manager = new BaselineManager(artifactsDir);
  const baseline = manager.createFromResults(mainSha, unit);
  manager.write(baseline);
  return { ok: true, baseline };
}
