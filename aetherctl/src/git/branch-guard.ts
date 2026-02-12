/**
 * Branch guard — enforces branch flow rules.
 *
 * Rules:
 * 1. No direct merge to main (must go through develop first)
 * 2. Only the Promoter actor may write to main
 */

export type BranchGuardResult = {
  allowed: boolean;
  reason?: string;
};

const PROTECTED_BRANCH = "main";
const REQUIRED_INTERMEDIATE = "develop";
const ALLOWED_MAIN_ACTOR = "promoter";

/** Check if a merge from source to target is allowed. */
export function checkMergeAllowed(
  source: string,
  target: string,
  actor: string,
): BranchGuardResult {
  // Rule 1: Only develop → main is allowed for the main branch
  if (target === PROTECTED_BRANCH && source !== REQUIRED_INTERMEDIATE) {
    return {
      allowed: false,
      reason: `Direct merge from '${source}' to '${PROTECTED_BRANCH}' is forbidden. Must go through '${REQUIRED_INTERMEDIATE}'.`,
    };
  }

  // Rule 2: Only Promoter may write to main
  if (target === PROTECTED_BRANCH && actor.toLowerCase() !== ALLOWED_MAIN_ACTOR) {
    return {
      allowed: false,
      reason: `Actor '${actor}' is not allowed to write to '${PROTECTED_BRANCH}'. Only '${ALLOWED_MAIN_ACTOR}' may do so.`,
    };
  }

  return { allowed: true };
}
