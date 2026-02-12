import { execFile } from "node:child_process";
import { promisify } from "node:util";

const pExecFile = promisify(execFile);

export type RegressResult = {
  lint: { pass: boolean; errors: number; warnings: number };
  unit: { pass: boolean; total: number; passed: number; failed: number; skipped: number; duration_ms: number };
};

/**
 * Regress step: run lint + unit tests.
 * Phase 1a runs only lint and unit tests.
 */
export async function runRegress(repoPath: string, commands?: { lint?: string; unit?: string }): Promise<RegressResult> {
  const lintCmd = commands?.lint ?? "pnpm lint";
  const unitCmd = commands?.unit ?? "pnpm test";

  const lint = await runLint(repoPath, lintCmd);
  const unit = await runUnit(repoPath, unitCmd);

  return { lint, unit };
}

async function runLint(
  repoPath: string,
  cmd: string,
): Promise<{ pass: boolean; errors: number; warnings: number }> {
  try {
    const [command, ...args] = cmd.split(/\s+/);
    await pExecFile(command, args, { cwd: repoPath, timeout: 120000 });
    return { pass: true, errors: 0, warnings: 0 };
  } catch (e: any) {
    const stderr = e?.stderr ?? "";
    const errorCount = (stderr.match(/error/gi) ?? []).length;
    const warnCount = (stderr.match(/warning/gi) ?? []).length;
    return { pass: false, errors: Math.max(errorCount, 1), warnings: warnCount };
  }
}

async function runUnit(
  repoPath: string,
  cmd: string,
): Promise<{ pass: boolean; total: number; passed: number; failed: number; skipped: number; duration_ms: number }> {
  const start = Date.now();
  try {
    const [command, ...args] = cmd.split(/\s+/);
    const { stdout } = await pExecFile(command, args, { cwd: repoPath, timeout: 300000 });
    const duration_ms = Date.now() - start;

    // Try to parse vitest/jest output for counts
    const counts = parseTestOutput(stdout);
    return { pass: true, duration_ms, ...counts };
  } catch (e: any) {
    const duration_ms = Date.now() - start;
    const stdout = e?.stdout ?? "";
    const counts = parseTestOutput(stdout);
    return { pass: false, duration_ms, ...counts };
  }
}

function parseTestOutput(stdout: string): { total: number; passed: number; failed: number; skipped: number } {
  // Match vitest output: "Tests  166 passed (166)"
  const passMatch = /(\d+)\s+passed/.exec(stdout);
  const failMatch = /(\d+)\s+failed/.exec(stdout);
  const skipMatch = /(\d+)\s+skipped/.exec(stdout);

  const passed = passMatch ? parseInt(passMatch[1], 10) : 0;
  const failed = failMatch ? parseInt(failMatch[1], 10) : 0;
  const skipped = skipMatch ? parseInt(skipMatch[1], 10) : 0;

  return { total: passed + failed + skipped, passed, failed, skipped };
}
