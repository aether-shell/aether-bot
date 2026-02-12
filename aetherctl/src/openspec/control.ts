import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { open, unlink } from "node:fs/promises";
import { computePlanDigest, ensureState, loadState, newRunId, updateState } from "./state.js";
import { runChange } from "./runner.js";
import { safePath, sanitizePathComponent } from "./security.js";

const pExecFile = promisify(execFile);

/**
 * Run manager to handle concurrent execution across multiple changes.
 * Uses file-based locks instead of in-memory state for robustness.
 */
class RunManager {
  private runningLocks = new Map<string, { lockPath: string; release: () => Promise<void> }>();

  async tryAcquireRun(repoPath: string, changeId: string): Promise<(() => Promise<void>) | null> {
    const key = `${repoPath}:${changeId}`;

    // Check if already running in this process
    if (this.runningLocks.has(key)) {
      return null;
    }

    try {
      const sanitizedChangeId = sanitizePathComponent(changeId);
      const lockPath = safePath(repoPath, "openspec", "changes", sanitizedChangeId, "run.lock");

      // Try to acquire lock (non-blocking)
      const fh = await open(lockPath, "wx");
      await fh.writeFile(`${process.pid}\n${Date.now()}\n`, "utf8");
      await fh.close();

      const release = async () => {
        try {
          await unlink(lockPath);
          this.runningLocks.delete(key);
        } catch (e) {
          console.error(`[openspec] Failed to release run lock: ${e}`);
        }
      };

      this.runningLocks.set(key, { lockPath, release });
      return release;
    } catch (e: any) {
      if (e?.code === "EEXIST") {
        return null; // Already running
      }
      throw e;
    }
  }
}

const runManager = new RunManager();

export async function approveStart(opts: {
  repoPath: string;
  changeId: string;
  requestedBy?: string | null;
}): Promise<{ ok: true; status: string } | { ok: false; error: string }> {
  const repoPath = opts.repoPath;
  const changeId = opts.changeId;

  const st = await ensureState(repoPath, changeId);
  if (st.status !== "WAIT_APPROVAL") {
    return { ok: false, error: `Change not in WAIT_APPROVAL (current: ${st.status})` };
  }

  // Try to acquire run lock
  const release = await runManager.tryAcquireRun(repoPath, changeId);
  if (!release) {
    return { ok: false, error: `BUSY (another process is running this change)` };
  }

  try {
    const digest = await computePlanDigest(repoPath, changeId);
    const headSha = await gitHeadSha(repoPath);
    const runId = newRunId();

    await updateState(repoPath, changeId, (prev) => {
      const now = new Date().toISOString();
      return {
        ...prev,
        plan: { planDigest: digest, headSha },
        approval: {
          approved: true,
          approvedAt: now,
          approvedBy: opts.requestedBy ?? null,
          approvedPlanDigest: digest
        },
        blocked: {
          ...prev.blocked,
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
        execution: {
          ...prev.execution,
          runId,
          attempt: (prev.execution.attempt ?? 0) + 1,
          startedAt: now,
          lastHeartbeatAt: now
        },
        status: "PRECHECK"
      };
    });

    const res = await runChange(repoPath, changeId);
    return res.ok ? { ok: true, status: res.status } : { ok: false, error: res.error };
  } finally {
    await release();
  }
}

export async function resumeChange(opts: {
  repoPath: string;
  changeId: string;
  userInput: string;
  requestedBy?: string | null;
}): Promise<{ ok: true; status: string } | { ok: false; error: string }> {
  const repoPath = opts.repoPath;
  const changeId = opts.changeId;

  const st = await ensureState(repoPath, changeId);
  if (["DONE", "CANCELLED"].includes(st.status)) return { ok: true, status: st.status };
  if (st.status === "FAILED") return { ok: false, error: "Change already FAILED" };
  if (st.status !== "PAUSED_UNEXPECTED") return { ok: false, error: `Change not in PAUSED_UNEXPECTED (current: ${st.status})` };
  if (!st.blocked.pause.request) return { ok: false, error: "No pause request present" };

  // Try to acquire run lock
  const release = await runManager.tryAcquireRun(repoPath, changeId);
  if (!release) {
    return { ok: false, error: `BUSY (another process is running this change)` };
  }

  try {
    const runId = st.execution.runId ?? newRunId();
    await updateState(repoPath, changeId, (prev) => {
      const now = new Date().toISOString();
      return {
        ...prev,
        blocked: {
          ...prev.blocked,
          pause: {
            ...prev.blocked.pause,
            userInput: opts.userInput,
            resolvedAt: now
          }
        },
        error: { code: null, message: null, detail: null, retryable: null, firstSeenAt: prev.error.firstSeenAt, lastSeenAt: null },
        execution: { ...prev.execution, runId, lastHeartbeatAt: now },
        status: "PRECHECK"
      };
    });

    const res = await runChange(repoPath, changeId);
    return res.ok ? { ok: true, status: res.status } : { ok: false, error: res.error };
  } finally {
    await release();
  }
}

export async function cancelChange(opts: {
  repoPath: string;
  changeId: string;
}): Promise<{ ok: true; status: string } | { ok: false; error: string }> {
  const st = await ensureState(opts.repoPath, opts.changeId);
  if (["DONE", "CANCELLED", "FAILED"].includes(st.status)) return { ok: true, status: st.status };

  const next = await updateState(opts.repoPath, opts.changeId, (prev) => {
    if (["WAIT_APPROVAL", "PLANNING", "PAUSED_UNEXPECTED"].includes(prev.status)) {
      return { ...prev, status: "CANCELLED" };
    }
    return { ...prev, status: "CANCEL_REQUESTED" };
  });

  return { ok: true, status: next.status };
}

export async function statusChange(opts: {
  repoPath: string;
  changeId: string;
}): Promise<{ ok: true; state: unknown; progressTail?: string[] } | { ok: false; error: string }> {
  try {
    const st = await loadState(opts.repoPath, opts.changeId);
    return { ok: true, state: st };
  } catch (e: any) {
    return { ok: false, error: e?.message ?? String(e) };
  }
}

async function gitHeadSha(repoPath: string): Promise<string> {
  const { stdout } = await pExecFile("git", ["rev-parse", "HEAD"], { cwd: repoPath });
  return stdout.trim();
}
