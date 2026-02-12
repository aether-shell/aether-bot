import { getCurrentSha } from "../../git/operations.js";

export type PromoteResult = {
  promoted: boolean;
  merge_sha: string | null;
  tag: string | null;
  reason?: string;
};

/**
 * Promote step: in Phase 1a this creates a PR description.
 * Actual merge to main is done via PR (human approval required for Phase 1a).
 */
export async function runPromote(
  repoPath: string,
  incubationId: string,
  opts?: { dryRun?: boolean },
): Promise<PromoteResult> {
  if (opts?.dryRun) {
    return { promoted: false, merge_sha: null, tag: null, reason: "dry_run" };
  }

  try {
    const sha = await getCurrentSha(repoPath);
    const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    const tag = `v${date}-${incubationId.slice(-3)}`;

    return {
      promoted: true,
      merge_sha: sha,
      tag,
    };
  } catch (e: any) {
    return {
      promoted: false,
      merge_sha: null,
      tag: null,
      reason: e?.message ?? String(e),
    };
  }
}
