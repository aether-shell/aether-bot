/**
 * CLI exit codes (§13).
 */
export const EXIT = {
  SUCCESS: 0,
  INCUBATION_FAILED: 1,
  JUDGE_REJECTED: 2,
  INVALID_ARGS: 3,
  CONCURRENT_CONFLICT: 4,
} as const;
