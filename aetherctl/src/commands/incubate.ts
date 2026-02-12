import path from "node:path";
import { loadConfig } from "../config/loader.js";
import { getCurrentSha } from "../git/operations.js";
import { generateIncubationId } from "../core/incubation-id.js";
import { Orchestrator, type StepRunner } from "../core/orchestrator.js";
import { EXIT } from "./exit-codes.js";
import type { ChangeType } from "../types/integration.js";
import type { RiskLevel } from "../types/integration.js";
import type { IncubationStep } from "../core/state-machine.js";
import { runFreeze } from "../core/steps/freeze.js";
import { runIntegrate } from "../core/steps/integrate.js";
import { runRegress } from "../core/steps/regress.js";
import { runPromote } from "../core/steps/promote.js";
import { BaselineManager } from "../judge/baseline-manager.js";

export type IncubateOpts = {
  branch: string;
  configDir: string;
  risk?: RiskLevel;
  type?: ChangeType;
  forceRestart?: boolean;
  dryRun?: boolean;
  format?: "human" | "jsonl";
};

export type IncubateResult =
  | { ok: true; incubationId: string; status: string }
  | { ok: false; error: string; exitCode: number };

export async function incubate(opts: IncubateOpts): Promise<IncubateResult> {
  try {
    const config = await loadConfig(opts.configDir);
    const artifactsDir = path.resolve(config.artifacts_dir || ".aether/artifacts");
    const repoPath = process.cwd();

    const baseSha = await getCurrentSha(repoPath);
    const incubationId = generateIncubationId(opts.branch, baseSha, artifactsDir);
    const riskLevel = opts.risk ?? "low";
    const changeType = opts.type ?? "feature";

    const baselineManager = new BaselineManager(artifactsDir);

    const stepRunner: StepRunner = async (step, state, cfg) => {
      switch (step as IncubationStep) {
        case "freeze":
          await runFreeze(repoPath, () => baselineManager.read());
          return { success: true };
        case "integrate":
          const intResult = await runIntegrate(repoPath, opts.branch, changeType);
          return intResult.success
            ? { success: true }
            : { success: false, error: `Merge conflicts: ${intResult.conflicts.join(", ")}` };
        case "regress":
          const regResult = await runRegress(repoPath);
          return regResult.unit.pass && regResult.lint.pass
            ? { success: true }
            : { success: false, error: "Tests or lint failed" };
        case "judge":
          // Judge step — in Phase 1a, pass if regress passed
          return { success: true };
        case "promote":
          const promResult = await runPromote(repoPath, incubationId, { dryRun: opts.dryRun });
          return promResult.promoted || opts.dryRun
            ? { success: true }
            : { success: false, error: promResult.reason ?? "Promote failed" };
        default:
          return { success: true };
      }
    };

    const orch = new Orchestrator(artifactsDir, config, stepRunner);
    const result = await orch.run({
      incubationId,
      sourceBranch: opts.branch,
      changeType,
      riskLevel,
      forceRestart: opts.forceRestart,
    });

    if (result.success) {
      return { ok: true, incubationId: result.incubation_id, status: String(result.final_status) };
    }

    const exitCode = result.final_status === "rejected" ? EXIT.JUDGE_REJECTED
      : result.error?.includes("Active incubation") ? EXIT.CONCURRENT_CONFLICT
      : EXIT.INCUBATION_FAILED;

    return { ok: false, error: result.error ?? "Incubation failed", exitCode };
  } catch (e: any) {
    return { ok: false, error: e?.message ?? String(e), exitCode: EXIT.INVALID_ARGS };
  }
}
