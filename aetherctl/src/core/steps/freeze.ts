import { getCurrentSha, getLockfileHash } from "../../git/operations.js";
import type { Baseline } from "../../types/baseline.js";

export type FreezeResult = {
  main_sha: string;
  lockfile_hash: string;
  captured_at: string;
  baseline: Baseline | null;
};

/**
 * Freeze step: capture main SHA, lockfile hash, and load existing baseline.
 */
export async function runFreeze(repoPath: string, readBaseline: () => Baseline | null): Promise<FreezeResult> {
  const main_sha = await getCurrentSha(repoPath);
  const lockfile_hash = await getLockfileHash(repoPath);
  const baseline = readBaseline();

  return {
    main_sha,
    lockfile_hash,
    captured_at: new Date().toISOString(),
    baseline,
  };
}
