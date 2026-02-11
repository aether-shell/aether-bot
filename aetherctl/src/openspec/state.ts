import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, rename, stat, unlink, utimes, type FileHandle } from "node:fs/promises";
import { dirname, join } from "node:path";
import { safePath, sanitizePathComponent, STALE_LOCK_AGE_MS } from "./security.js";

export type OpenSpecStatus =
  | "PLANNING"
  | "WAIT_APPROVAL"
  | "PRECHECK"
  | "EXECUTING"
  | "VERIFYING"
  | "CANCEL_REQUESTED"
  | "PAUSED_UNEXPECTED"
  | "DONE"
  | "CANCELLED"
  | "FAILED";

export type OpenSpecStateV1 = {
  schemaVersion: 1;
  changeId: string;
  repoPath: string;
  status: OpenSpecStatus;
  createdAt: string;
  updatedAt: string;

  approval: {
    approved: boolean;
    approvedAt: string | null;
    approvedBy: string | null;
    approvedPlanDigest: string | null;
  };

  plan: {
    planDigest: string | null;
    headSha: string | null;
  };

  execution: {
    runId: string | null;
    attempt: number;
    preflightPassed: boolean;
    currentTask: string | null;
    currentStepId: string | null;
    startedAt: string | null;
    lastHeartbeatAt: string | null;
  };

  loop: {
    lastTickAt: string | null;
    ticks: number;
    maxTicks: number;
    maxRunSeconds: number;
  };

  blocked: {
    reason: string | null;
    needs: unknown | null;
    pause: {
      kind: string | null;
      request: string | null;
      unblockCommand: string | null;
      resumeCondition: string | null;
      userInput: string | null;
      requestedAt: string | null;
      resolvedAt: string | null;
    };
  };

  error: {
    code: string | null;
    message: string | null;
    detail: unknown | null;
    retryable: boolean | null;
    firstSeenAt: string | null;
    lastSeenAt: string | null;
  };
};

export function statePathFor(repoPath: string, changeId: string): string {
  // Validate and sanitize changeId to prevent path traversal
  const sanitizedChangeId = sanitizePathComponent(changeId);
  return safePath(repoPath, "openspec", "changes", sanitizedChangeId, "state.json");
}

export function progressLogPathFor(repoPath: string, changeId: string): string {
  // Validate and sanitize changeId to prevent path traversal
  const sanitizedChangeId = sanitizePathComponent(changeId);
  return safePath(repoPath, "openspec", "changes", sanitizedChangeId, "progress.log");
}

export async function loadState(repoPath: string, changeId: string): Promise<OpenSpecStateV1> {
  const p = statePathFor(repoPath, changeId);
  const raw = await readFile(p, "utf8");
  const json = JSON.parse(raw) as OpenSpecStateV1;
  if (json.schemaVersion !== 1) throw new Error(`Unsupported state schemaVersion: ${(json as any).schemaVersion}`);
  return json;
}

export async function ensureState(repoPath: string, changeId: string): Promise<OpenSpecStateV1> {
  const p = statePathFor(repoPath, changeId);
  try {
    const s = await stat(p);
    if (s.isFile()) return await loadState(repoPath, changeId);
  } catch {
    // ignore
  }

  const now = new Date().toISOString();
  const init: OpenSpecStateV1 = {
    schemaVersion: 1,
    changeId,
    repoPath,
    status: "PLANNING",
    createdAt: now,
    updatedAt: now,
    approval: { approved: false, approvedAt: null, approvedBy: null, approvedPlanDigest: null },
    plan: { planDigest: null, headSha: null },
    execution: {
      runId: null,
      attempt: 0,
      preflightPassed: false,
      currentTask: null,
      currentStepId: null,
      startedAt: null,
      lastHeartbeatAt: null
    },
    loop: { lastTickAt: null, ticks: 0, maxTicks: 200, maxRunSeconds: 1800 },
    blocked: {
      reason: null,
      needs: null,
      pause: {
        kind: null,
        request: null,
        unblockCommand: null,
        resumeCondition: null,
        userInput: null,
        requestedAt: null,
        resolvedAt: null
      }
    },
    error: { code: null, message: null, detail: null, retryable: null, firstSeenAt: null, lastSeenAt: null }
  };

  await atomicWriteJson(p, init);
  return init;
}

export async function atomicWriteJson(path: string, data: unknown): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const tmp = `${path}.tmp.${process.pid}.${Date.now()}`;
  const payload = JSON.stringify(data, null, 2) + "\n";

  let fh: FileHandle | null = null;
  try {
    fh = await open(tmp, "w");
    await fh.writeFile(payload, "utf8");
    await fh.sync();
    await fh.close();
    fh = null;

    await rename(tmp, path);
  } catch (e) {
    // Clean up temporary file on error
    try {
      if (fh) await fh.close();
      await unlink(tmp);
    } catch {
      // Ignore cleanup errors
    }
    throw e;
  }
}

export async function updateState(
  repoPath: string,
  changeId: string,
  fn: (prev: OpenSpecStateV1) => OpenSpecStateV1
): Promise<OpenSpecStateV1> {
  const statePath = statePathFor(repoPath, changeId);
  const lockPath = `${statePath}.lock`;

  const release = await acquireFsLock(lockPath, 5000);
  try {
    const prev = await ensureState(repoPath, changeId);
    const next = fn(prev);
    next.updatedAt = new Date().toISOString();
    await atomicWriteJson(statePath, next);
    return next;
  } finally {
    await release();
  }
}

async function acquireFsLock(lockPath: string, timeoutMs: number): Promise<() => Promise<void>> {
  await mkdir(dirname(lockPath), { recursive: true });
  const started = Date.now();
  const pid = process.pid;
  const maxRetries = Math.ceil(timeoutMs / 50);
  let retries = 0;

  while (retries < maxRetries) {
    try {
      // Check for and clean up stale locks
      try {
        const stats = await stat(lockPath);
        const age = Date.now() - stats.mtimeMs;
        if (age > STALE_LOCK_AGE_MS) {
          console.warn(`[openspec] Removing stale lock (age: ${Math.round(age / 1000)}s): ${lockPath}`);
          await unlink(lockPath);
        } else {
          // Lock exists and is not stale, check if owner process is alive
          const content = await readFile(lockPath, "utf8").catch(() => "");
          const [lockPid] = content.split("\n");
          if (lockPid && lockPid !== String(pid)) {
            // Try to check if process exists (Unix-like systems)
            try {
              process.kill(Number(lockPid), 0); // Signal 0 checks existence
              // Process exists, lock is valid
            } catch {
              // Process doesn't exist, lock is orphaned
              console.warn(`[openspec] Removing orphaned lock (pid: ${lockPid}): ${lockPath}`);
              await unlink(lockPath);
            }
          }
        }
      } catch {
        // Lock file doesn't exist or error reading, continue to acquire
      }

      // Atomically create lock file with PID and timestamp
      const fh = await open(lockPath, "wx");
      try {
        await fh.writeFile(`${pid}\n${Date.now()}\n`, "utf8");
        await fh.sync();
      } finally {
        await fh.close();
      }

      // Successfully acquired lock, return release function
      return async () => {
        try {
          // Verify lock still belongs to us before releasing
          const content = await readFile(lockPath, "utf8");
          const [lockPid] = content.split("\n");
          if (lockPid === String(pid)) {
            await unlink(lockPath);
          } else {
            console.warn(`[openspec] Lock was taken by another process (current: ${lockPid}, ours: ${pid}): ${lockPath}`);
          }
        } catch (e) {
          console.error(`[openspec] Failed to release lock: ${e}`);
          throw e; // Re-throw to make lock release failures visible
        }
      };
    } catch (e: any) {
      if (e?.code !== "EEXIST") throw e;

      retries++;
      if (Date.now() - started > timeoutMs || retries >= maxRetries) {
        throw new Error(`Timed out acquiring state lock after ${retries} retries: ${lockPath}`);
      }

      // Exponential backoff with jitter
      const backoff = Math.min(50 * Math.pow(1.5, retries), 1000);
      const jitter = Math.random() * backoff * 0.1;
      await new Promise((r) => setTimeout(r, backoff + jitter));
    }
  }

  throw new Error(`Failed to acquire lock after ${retries} retries: ${lockPath}`);
}

export async function computePlanDigest(repoPath: string, changeId: string): Promise<string> {
  // Sanitize changeId to prevent path traversal
  const sanitizedChangeId = sanitizePathComponent(changeId);

  const parts: Array<[string, string]> = [];
  const files = [
    safePath(repoPath, "openspec", "config.yaml"),
    safePath(repoPath, "openspec", "changes", sanitizedChangeId, "proposal.md"),
    safePath(repoPath, "openspec", "changes", sanitizedChangeId, "design.md"),
    safePath(repoPath, "openspec", "changes", sanitizedChangeId, "tasks.md")
  ];

  for (const f of files) {
    let content = "";
    try {
      content = await readFile(f, "utf8");
    } catch {
      content = "";
    }
    parts.push([f, normalizeText(content)]);
  }

  const h = createHash("sha256");
  for (const [f, c] of parts) {
    h.update(f);
    h.update("\n");
    h.update(c);
    h.update("\n\n");
  }
  return h.digest("hex");
}

export function newRunId(): string {
  return randomUUID();
}

function normalizeText(s: string): string {
  return s.replace(/\r\n/g, "\n").trimEnd() + "\n";
}
