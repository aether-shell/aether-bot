import { approveStart, cancelChange, resumeChange, statusChange } from "../openspec/control.js";
import { redactSensitiveInfo } from "../openspec/security.js";

/**
 * Wraps an openspec command with unified error handling:
 * - Checks `ok` on the result, throws redacted error if false
 * - Writes success JSON to stdout
 * - Catches all errors, writes redacted error to stderr, re-throws
 */
async function wrapCommand<T extends { ok: boolean; error?: string }>(
  fn: () => Promise<T>,
  formatSuccess: (res: T) => Record<string, unknown>,
): Promise<void> {
  try {
    const res = await fn();
    if (!res.ok) {
      const error = redactSensitiveInfo(res.error ?? "Unknown error");
      throw new Error(error);
    }
    process.stdout.write(JSON.stringify(formatSuccess(res)) + "\n");
  } catch (e: any) {
    const error = redactSensitiveInfo(e?.message ?? String(e));
    process.stderr.write(JSON.stringify({ ok: false, error }) + "\n");
    throw new Error(error);
  }
}

export async function openspecApproveStart(opts: {
  repoPath: string;
  changeId: string;
  requestedBy?: string | null;
}): Promise<void> {
  await wrapCommand(
    () => approveStart({ repoPath: opts.repoPath, changeId: opts.changeId, requestedBy: opts.requestedBy ?? null }),
    (res) => ({ ok: true, status: (res as any).status }),
  );
}

export async function openspecResume(opts: {
  repoPath: string;
  changeId: string;
  userInput: string;
  requestedBy?: string | null;
}): Promise<void> {
  await wrapCommand(
    () => resumeChange({ repoPath: opts.repoPath, changeId: opts.changeId, userInput: opts.userInput, requestedBy: opts.requestedBy ?? null }),
    (res) => ({ ok: true, status: (res as any).status }),
  );
}

export async function openspecCancel(opts: { repoPath: string; changeId: string }): Promise<void> {
  await wrapCommand(
    () => cancelChange({ repoPath: opts.repoPath, changeId: opts.changeId }),
    (res) => ({ ok: true, status: (res as any).status }),
  );
}

export async function openspecStatus(opts: { repoPath: string; changeId: string }): Promise<void> {
  await wrapCommand(
    () => statusChange({ repoPath: opts.repoPath, changeId: opts.changeId }),
    (res) => ({ ok: true, state: (res as any).state }),
  );
}
