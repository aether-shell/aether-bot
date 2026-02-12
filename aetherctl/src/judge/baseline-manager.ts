import fs from "node:fs";
import path from "node:path";
import type { Baseline, TestStats } from "../types/baseline.js";

/**
 * Baseline Manager — reads, creates, and updates baseline snapshots.
 */
export class BaselineManager {
  private readonly baselinePath: string;

  constructor(artifactsDir: string) {
    this.baselinePath = path.join(artifactsDir, "baseline.json");
  }

  /** Read the current baseline. Returns null if no baseline exists (first run). */
  read(): Baseline | null {
    if (!fs.existsSync(this.baselinePath)) return null;
    const raw = fs.readFileSync(this.baselinePath, "utf8");
    return JSON.parse(raw) as Baseline;
  }

  /** Write/update the baseline after a successful promotion. */
  write(baseline: Baseline): void {
    fs.mkdirSync(path.dirname(this.baselinePath), { recursive: true });
    fs.writeFileSync(this.baselinePath, JSON.stringify(baseline, null, 2), "utf8");
  }

  /**
   * Create a baseline from current test results after promotion.
   */
  createFromResults(mainSha: string, unit?: TestStats, integration?: TestStats): Baseline {
    const baseline: Baseline = {
      main_sha: mainSha,
      captured_at: new Date().toISOString(),
      tests: {},
      flaky_tests: [],
    };
    if (unit) baseline.tests.unit = unit;
    if (integration) baseline.tests.integration = integration;
    return baseline;
  }

  /**
   * Get a default baseline for first-run scenarios.
   * Uses the candidate's own results as the baseline (no regression possible).
   */
  getFirstRunBaseline(): { total: number; passed: number; failed: number; duration_ms: number } {
    return { total: 0, passed: 0, failed: 0, duration_ms: 0 };
  }

  /** Mark a test as flaky in the baseline. */
  markFlaky(testName: string): void {
    const baseline = this.read();
    if (!baseline) return;
    if (!baseline.flaky_tests) baseline.flaky_tests = [];
    if (!baseline.flaky_tests.includes(testName)) {
      baseline.flaky_tests.push(testName);
      this.write(baseline);
    }
  }
}
