import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { computePlanDigest, loadState, progressLogPathFor, updateState } from "./state.js";
import {
  validateCommand,
  sanitizeLogMessage,
  redactSensitiveInfo,
  validateTasksContent,
  sanitizeEnv,
  safePath,
  sanitizePathComponent,
  MAX_TASKS_FILE_SIZE,
  MAX_TASK_COUNT,
  MAX_LINE_LENGTH,
} from "./security.js";

const pExecFile = promisify(execFile);

type RunResult = { ok: true; status: string } | { ok: false; status: string; error: string };

export async function runChange(repoPath: string, changeId: string): Promise<RunResult> {
  const startedAt = Date.now();

  // Plan drift check
  const currentDigest = await computePlanDigest(repoPath, changeId);
  const st0 = await loadState(repoPath, changeId);
  if (!st0.approval.approvedPlanDigest || st0.approval.approvedPlanDigest !== currentDigest) {
    await updateState(repoPath, changeId, (s) => ({
      ...s,
      status: "WAIT_APPROVAL",
      blocked: { ...s.blocked, reason: "PLAN_DRIFT", needs: { approvedPlanDigest: s.approval.approvedPlanDigest, currentPlanDigest: currentDigest } }
    }));
    return { ok: false, status: "WAIT_APPROVAL", error: "PLAN_DRIFT" };
  }

  // Preflight gate (minimal, implementable): ensure artifacts exist.
  const missing = await findMissingArtifacts(repoPath, changeId);
  if (missing.length > 0) {
    await updateState(repoPath, changeId, (s) => ({
      ...s,
      status: "WAIT_APPROVAL",
      execution: { ...s.execution, preflightPassed: false },
      blocked: { ...s.blocked, reason: "PREFLIGHT_FAILED", needs: { missingArtifacts: missing } }
    }));
    await log(repoPath, changeId, st0.execution.runId, `PREFLIGHT_FAILED missing=${missing.join(",")}`);
    return { ok: false, status: "WAIT_APPROVAL", error: "PREFLIGHT_FAILED" };
  }

  // Validate tasks.md content before execution
  const sanitizedChangeId = sanitizePathComponent(changeId);
  const tasksPath = safePath(repoPath, "openspec", "changes", sanitizedChangeId, "tasks.md");
  try {
    const tasksContent = await readFile(tasksPath, "utf8");
    validateTasksContent(tasksContent);
  } catch (e: any) {
    await updateState(repoPath, changeId, (s) => ({
      ...s,
      status: "FAILED",
      error: {
        code: "INVALID_TASKS",
        message: redactSensitiveInfo(e?.message ?? String(e)),
        detail: null,
        retryable: false,
        firstSeenAt: s.error.firstSeenAt ?? new Date().toISOString(),
        lastSeenAt: new Date().toISOString()
      }
    }));
    return { ok: false, status: "FAILED", error: "INVALID_TASKS" };
  }

  await updateState(repoPath, changeId, (s) => ({
    ...s,
    status: "EXECUTING",
    execution: { ...s.execution, preflightPassed: true, lastHeartbeatAt: new Date().toISOString() }
  }));

  // Apply loop (Phase 1 minimal): walk tasks.md checkboxes, execute validated commands; mark them done.
  const loopMaxTicks = st0.loop?.maxTicks ?? 200;
  const loopMaxSeconds = st0.loop?.maxRunSeconds ?? 1800;

  for (let tick = 0; tick < loopMaxTicks; tick++) {
    if ((Date.now() - startedAt) / 1000 > loopMaxSeconds) {
      await fail(repoPath, changeId, "TIMEOUT", `maxRunSeconds=${loopMaxSeconds}`);
      return { ok: false, status: "FAILED", error: "TIMEOUT" };
    }

    // Check for cancellation atomically within state update
    const st = await loadState(repoPath, changeId);
    if (st.status === "CANCEL_REQUESTED") {
      await updateState(repoPath, changeId, (s) => ({ ...s, status: "CANCELLED" }));
      await log(repoPath, changeId, st.execution.runId, "CANCELLED");
      return { ok: true, status: "CANCELLED" };
    }

    const tasksRaw = await readFile(tasksPath, "utf8");
    const next = findNextUncheckedTask(tasksRaw);
    await updateState(repoPath, changeId, (s) => ({
      ...s,
      loop: { ...s.loop, ticks: tick + 1, lastTickAt: new Date().toISOString() },
      execution: { ...s.execution, currentTask: next?.text ?? null, currentStepId: next?.stepId ?? null, lastHeartbeatAt: new Date().toISOString() }
    }));

    if (!next) break;

    // Validated command execution with proper error handling
    if (next.cmd) {
      try {
        // Validate command against whitelist
        const { command, args } = validateCommand(next.cmd);

        await log(repoPath, changeId, st.execution.runId, `STEP ${next.stepId} RUN ${redactSensitiveInfo(next.cmd)}`);

        // Execute without shell, with sanitized environment
        const { stdout, stderr } = await pExecFile(command, args, {
          cwd: repoPath,
          maxBuffer: 50 * 1024 * 1024, // 50MB
          shell: false, // Critical: disable shell to prevent injection
          env: sanitizeEnv(process.env),
          timeout: 300000, // 5 minutes per command
        });

        if (stdout.trim()) {
          const sanitized = sanitizeLogMessage(stdout.trim());
          await log(repoPath, changeId, st.execution.runId, `STDOUT ${sanitized.slice(0, 5000)}`);
        }
        if (stderr.trim()) {
          const sanitized = sanitizeLogMessage(stderr.trim());
          await log(repoPath, changeId, st.execution.runId, `STDERR ${sanitized.slice(0, 5000)}`);
        }
      } catch (e: any) {
        const errorDetails = {
          message: redactSensitiveInfo(e?.message ?? String(e)),
          code: e?.code,
          signal: e?.signal,
          killed: e?.killed,
          command: redactSensitiveInfo(next.cmd),
        };

        await log(repoPath, changeId, st.execution.runId, `ERROR ${JSON.stringify(errorDetails)}`);
        await pauseUnexpected(
          repoPath,
          changeId,
          "CMD_FAILED",
          `Command failed: ${redactSensitiveInfo(next.cmd)}`,
          `Fix the issue and resume. Error: ${errorDetails.message}`,
          errorDetails
        );
        return { ok: false, status: "PAUSED_UNEXPECTED", error: "CMD_FAILED" };
      }
    } else {
      await log(repoPath, changeId, st.execution.runId, `STEP ${next.stepId} COMPLETE (no-op)`);
    }

    const updated = markTaskChecked(tasksRaw, next.index);
    await atomicWriteText(tasksPath, updated);
  }

  await updateState(repoPath, changeId, (s) => ({ ...s, status: "VERIFYING", execution: { ...s.execution, lastHeartbeatAt: new Date().toISOString() } }));

  // Verify gate (minimal): `git status --porcelain` must succeed.
  try {
    await pExecFile("git", ["status", "--porcelain=v1"], { cwd: repoPath });
  } catch (e: any) {
    await fail(repoPath, changeId, "VERIFY_FAILED", e?.message ?? String(e));
    return { ok: false, status: "FAILED", error: "VERIFY_FAILED" };
  }

  await updateState(repoPath, changeId, (s) => ({ ...s, status: "DONE", execution: { ...s.execution, currentTask: null, currentStepId: null, lastHeartbeatAt: new Date().toISOString() } }));
  await log(repoPath, changeId, st0.execution.runId, "DONE");
  return { ok: true, status: "DONE" };
}

async function findMissingArtifacts(repoPath: string, changeId: string): Promise<string[]> {
  const sanitizedChangeId = sanitizePathComponent(changeId);
  const base = safePath(repoPath, "openspec", "changes", sanitizedChangeId);
  const required = ["proposal.md", "design.md", "tasks.md", "state.json"];
  const missing: string[] = [];
  for (const f of required) {
    try {
      await readFile(safePath(base, f), "utf8");
    } catch {
      missing.push(f);
    }
  }
  return missing;
}

type NextTask = { index: number; text: string; stepId: string; cmd: string | null };

function findNextUncheckedTask(tasksMd: string): NextTask | null {
  if (tasksMd.length > MAX_TASKS_FILE_SIZE) {
    throw new Error(`Tasks file too large: ${tasksMd.length} bytes`);
  }

  const lines = tasksMd.split("\n");
  if (lines.length > MAX_TASK_COUNT) {
    throw new Error(`Too many tasks: ${lines.length}`);
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.length > MAX_LINE_LENGTH) {
      throw new Error(`Line too long at ${i + 1}: ${line.length} chars`);
    }

    const m = /^\s*-\s*\[ \]\s*(.+)\s*$/.exec(line);
    if (!m) continue;

    const text = m[1].trim();
    if (text.length === 0) continue;

    const stepId = `L${i + 1}`;
    const cmd = parseCmd(text);
    return { index: i, text, stepId, cmd };
  }
  return null;
}

function parseCmd(text: string): string | null {
  const m = /^cmd:\s*(.+)$/.exec(text);
  if (!m) return null;
  return m[1].trim();
}

function markTaskChecked(tasksMd: string, lineIndex: number): string {
  const lines = tasksMd.split("\n");
  lines[lineIndex] = lines[lineIndex].replace("[ ]", "[x]");
  return lines.join("\n");
}

async function atomicWriteText(path: string, content: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const tmp = `${path}.tmp.${process.pid}.${Date.now()}`;
  await writeFile(tmp, content.endsWith("\n") ? content : content + "\n", "utf8");
  await renameFile(tmp, path);
}

async function renameFile(tmp: string, dest: string): Promise<void> {
  const { rename } = await import("node:fs/promises");
  await rename(tmp, dest);
}

async function log(repoPath: string, changeId: string, runId: string | null, msg: string): Promise<void> {
  const p = progressLogPathFor(repoPath, changeId);
  await mkdir(dirname(p), { recursive: true });

  const sanitized = {
    runId: sanitizeLogMessage(runId ?? "-"),
    changeId: sanitizeLogMessage(changeId),
    repoPath: sanitizeLogMessage(repoPath),
    msg: sanitizeLogMessage(msg),
  };

  const line = `[${new Date().toISOString()}] runId=${sanitized.runId} changeId=${sanitized.changeId} repoPath=${sanitized.repoPath} ${sanitized.msg}\n`;
  await appendFile(p, line, "utf8");
}

async function pauseUnexpected(
  repoPath: string,
  changeId: string,
  code: string,
  request: string,
  resumeCondition: string,
  detail?: unknown
): Promise<void> {
  await updateState(repoPath, changeId, (s) => ({
    ...s,
    status: "PAUSED_UNEXPECTED",
    blocked: {
      ...s.blocked,
      reason: code,
      needs: null,
      pause: {
        kind: code,
        request,
        unblockCommand: null,
        resumeCondition,
        userInput: null,
        requestedAt: new Date().toISOString(),
        resolvedAt: null
      }
    },
    error: {
      code,
      message: redactSensitiveInfo(request),
      detail: detail ?? null,
      retryable: true,
      firstSeenAt: s.error.firstSeenAt ?? new Date().toISOString(),
      lastSeenAt: new Date().toISOString()
    }
  }));
}

async function fail(repoPath: string, changeId: string, code: string, detail: unknown): Promise<void> {
  const message = typeof detail === "string" ? redactSensitiveInfo(detail) : code;
  await updateState(repoPath, changeId, (s) => ({
    ...s,
    status: "FAILED",
    error: {
      code,
      message,
      detail,
      retryable: false,
      firstSeenAt: s.error.firstSeenAt ?? new Date().toISOString(),
      lastSeenAt: new Date().toISOString()
    }
  }));
}
