import { mergeBranch } from "../../git/operations.js";
import type { ChangeType } from "../../types/integration.js";

export type IntegrateResult = {
  source_branch: string;
  target_branch: string;
  change_type: ChangeType;
  merge_sha: string | null;
  conflicts: string[];
  success: boolean;
};

/**
 * Integrate step: merge source branch into develop.
 */
export async function runIntegrate(
  repoPath: string,
  sourceBranch: string,
  changeType: ChangeType,
): Promise<IntegrateResult> {
  const targetBranch = "develop";

  try {
    const mergeResult = await mergeBranch(repoPath, sourceBranch);
    return {
      source_branch: sourceBranch,
      target_branch: targetBranch,
      change_type: changeType,
      merge_sha: mergeResult.sha,
      conflicts: mergeResult.conflicts ?? [],
      success: mergeResult.conflicts == null || mergeResult.conflicts.length === 0,
    };
  } catch (e: any) {
    return {
      source_branch: sourceBranch,
      target_branch: targetBranch,
      change_type: changeType,
      merge_sha: null,
      conflicts: [e?.message ?? String(e)],
      success: false,
    };
  }
}
