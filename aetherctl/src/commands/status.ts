import fs from "node:fs";
import path from "node:path";
import type { IncubationState } from "../core/orchestrator.js";

export type StatusResult =
  | { ok: true; state: IncubationState }
  | { ok: false; error: string };

/**
 * Read incubation state for a given ID.
 */
export function status(opts: { artifactsDir: string; incubationId: string }): StatusResult {
  const statePath = path.join(opts.artifactsDir, opts.incubationId, "state.json");

  if (!fs.existsSync(statePath)) {
    return { ok: false, error: `No incubation found: ${opts.incubationId}` };
  }

  try {
    const raw = fs.readFileSync(statePath, "utf8");
    const state = JSON.parse(raw) as IncubationState;
    return { ok: true, state };
  } catch (e: any) {
    return { ok: false, error: `Failed to read state: ${e?.message ?? String(e)}` };
  }
}

/**
 * List all incubations with their current status.
 */
export function listIncubations(artifactsDir: string): Array<{ id: string; status: string; updated_at: string }> {
  if (!fs.existsSync(artifactsDir)) return [];

  const entries = fs.readdirSync(artifactsDir, { withFileTypes: true });
  const results: Array<{ id: string; status: string; updated_at: string }> = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const statePath = path.join(artifactsDir, entry.name, "state.json");
    if (!fs.existsSync(statePath)) continue;

    try {
      const raw = fs.readFileSync(statePath, "utf8");
      const state = JSON.parse(raw);
      results.push({
        id: entry.name,
        status: String(state.current_step ?? "unknown"),
        updated_at: String(state.updated_at ?? ""),
      });
    } catch {
      results.push({ id: entry.name, status: "corrupted", updated_at: "" });
    }
  }

  return results.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}
