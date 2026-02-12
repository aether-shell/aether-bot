import fs from "node:fs";
import path from "node:path";

export type ConcurrencyCheck = {
  allowed: boolean;
  activeId?: string;
  reason?: string;
};

/**
 * Phase 1a concurrency lock: only one active incubation at a time.
 * Checks artifacts dir for any state.json with status not in terminal states.
 */
export function checkConcurrency(artifactsDir: string, maxConcurrent: number = 1): ConcurrencyCheck {
  if (!fs.existsSync(artifactsDir)) {
    return { allowed: true };
  }

  const entries = fs.readdirSync(artifactsDir, { withFileTypes: true });
  const terminalStatuses = new Set(["done", "rejected", "cancelled"]);
  let activeCount = 0;

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;

    const statePath = path.join(artifactsDir, entry.name, "state.json");
    if (!fs.existsSync(statePath)) continue;

    try {
      const raw = fs.readFileSync(statePath, "utf8");
      const state = JSON.parse(raw);
      const status = String(state.current_step ?? state.status ?? "").toLowerCase();

      if (!terminalStatuses.has(status) && !status.startsWith("failed_") && !status.startsWith("timeout_")) {
        activeCount++;
        if (activeCount >= maxConcurrent) {
          return {
            allowed: false,
            activeId: entry.name,
            reason: `Active incubation already running: ${entry.name} (status: ${status})`,
          };
        }
      }
    } catch {
      // Corrupted state file — skip
    }
  }

  return { allowed: true };
}
