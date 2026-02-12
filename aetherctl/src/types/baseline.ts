/** Baseline snapshot — last successful promotion metrics. (V3 §7.2) */
export type TestStats = {
  total: number;
  passed: number;
  failed: number;
  duration_ms: number;
};

export type Baseline = {
  main_sha: string;
  captured_at: string;
  tests: {
    unit?: TestStats;
    integration?: TestStats;
  };
  flaky_tests?: string[];
};
