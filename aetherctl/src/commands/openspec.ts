import { approveStart, cancelChange, resumeChange, statusChange } from "../openspec/control.js";
import { redactSensitiveInfo } from "../openspec/security.js";

export async function openspecApproveStart(opts: {
  repoPath: string;
  changeId: string;
  requestedBy?: string | null;
}): Promise<void> {
  try {
    const res = await approveStart({ repoPath: opts.repoPath, changeId: opts.changeId, requestedBy: opts.requestedBy ?? null });
    if (!res.ok) {
      const error = redactSensitiveInfo(res.error);
      throw new Error(error);
    }
    process.stdout.write(JSON.stringify({ ok: true, status: res.status }) + "\n");
  } catch (e: any) {
    const error = redactSensitiveInfo(e?.message ?? String(e));
    process.stderr.write(JSON.stringify({ ok: false, error }) + "\n");
    throw new Error(error);
  }
}

export async function openspecResume(opts: {
  repoPath: string;
  changeId: string;
  userInput: string;
  requestedBy?: string | null;
}): Promise<void> {
  try {
    const res = await resumeChange({
      repoPath: opts.repoPath,
      changeId: opts.changeId,
      userInput: opts.userInput,
      requestedBy: opts.requestedBy ?? null
    });
    if (!res.ok) {
      const error = redactSensitiveInfo(res.error);
      throw new Error(error);
    }
    process.stdout.write(JSON.stringify({ ok: true, status: res.status }) + "\n");
  } catch (e: any) {
    const error = redactSensitiveInfo(e?.message ?? String(e));
    process.stderr.write(JSON.stringify({ ok: false, error }) + "\n");
    throw new Error(error);
  }
}

export async function openspecCancel(opts: { repoPath: string; changeId: string }): Promise<void> {
  try {
    const res = await cancelChange({ repoPath: opts.repoPath, changeId: opts.changeId });
    if (!res.ok) {
      const error = redactSensitiveInfo(res.error);
      throw new Error(error);
    }
    process.stdout.write(JSON.stringify({ ok: true, status: res.status }) + "\n");
  } catch (e: any) {
    const error = redactSensitiveInfo(e?.message ?? String(e));
    process.stderr.write(JSON.stringify({ ok: false, error }) + "\n");
    throw new Error(error);
  }
}

export async function openspecStatus(opts: { repoPath: string; changeId: string }): Promise<void> {
  try {
    const res = await statusChange({ repoPath: opts.repoPath, changeId: opts.changeId });
    if (!res.ok) {
      const error = redactSensitiveInfo(res.error);
      throw new Error(error);
    }
    process.stdout.write(JSON.stringify({ ok: true, state: res.state }) + "\n");
  } catch (e: any) {
    const error = redactSensitiveInfo(e?.message ?? String(e));
    process.stderr.write(JSON.stringify({ ok: false, error }) + "\n");
    throw new Error(error);
  }
}
