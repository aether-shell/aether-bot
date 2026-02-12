import { describe, expect, it } from "vitest";
import { classifyRisk } from "../src/risk/classifier.js";
import { checkAutoEscalation } from "../src/risk/auto-escalation.js";
import type { RiskPolicyConfig } from "../src/types/config.js";

const POLICY: RiskPolicyConfig = {
  path_rules: [
    { pattern: "migrations/*", level: "high" },
    { pattern: "docs/*", level: "low" },
    { pattern: "*.md", level: "low" },
    { pattern: "src/auth/*", level: "high" },
    { pattern: "src/security/*", level: "high" },
    { pattern: ".github/*", level: "low" },
  ],
  auto_escalation: [
    "migrations/*",
    "src/auth/*",
    "src/security/*",
    "src/judge/*",
    "src/promoter/*",
  ],
};

describe("risk classifier", () => {
  it("classifies docs/ changes as low", () => {
    const result = classifyRisk(
      { files_changed: ["docs/README.md"], change_type: "feature", lines_added: 10, lines_removed: 0 },
      POLICY,
    );
    expect(result.level).toBe("low");
    expect(result.auto_escalated).toBe(false);
  });

  it("classifies migrations/ as high via auto-escalation", () => {
    const result = classifyRisk(
      { files_changed: ["migrations/001.sql"], change_type: "feature", lines_added: 5, lines_removed: 0 },
      POLICY,
    );
    expect(result.level).toBe("high");
    expect(result.auto_escalated).toBe(true);
    expect(result.reason).toContain("Auto-escalated");
  });

  it("classifies src/auth/ as high via auto-escalation", () => {
    const result = classifyRisk(
      { files_changed: ["src/auth/login.ts"], change_type: "bugfix", lines_added: 20, lines_removed: 5 },
      POLICY,
    );
    expect(result.level).toBe("high");
    expect(result.auto_escalated).toBe(true);
  });

  it("bumps dependency change_type to medium", () => {
    const result = classifyRisk(
      { files_changed: ["package.json"], change_type: "dependency", lines_added: 3, lines_removed: 3 },
      POLICY,
    );
    expect(result.level).toBe("medium");
    expect(result.reason).toContain("Dependency");
  });

  it("bumps large changes (>500 lines) to medium", () => {
    const result = classifyRisk(
      { files_changed: ["src/foo.ts"], change_type: "refactor", lines_added: 400, lines_removed: 200 },
      POLICY,
    );
    expect(result.level).toBe("medium");
    expect(result.reason).toContain("Large change");
  });

  it("defaults to low when no rules match", () => {
    const result = classifyRisk(
      { files_changed: ["src/utils/helper.ts"], change_type: "feature", lines_added: 10, lines_removed: 2 },
      POLICY,
    );
    expect(result.level).toBe("low");
    expect(result.auto_escalated).toBe(false);
  });

  it("takes highest severity across multiple files", () => {
    const result = classifyRisk(
      { files_changed: ["docs/guide.md", "src/security/crypto.ts"], change_type: "feature", lines_added: 10, lines_removed: 0 },
      POLICY,
    );
    expect(result.level).toBe("high");
    expect(result.auto_escalated).toBe(true);
  });
});

describe("auto-escalation", () => {
  it("detects escalation for matching files", () => {
    const result = checkAutoEscalation(["src/judge/judge.ts"], POLICY.auto_escalation);
    expect(result.escalated).toBe(true);
    expect(result.matchedFiles).toContain("src/judge/judge.ts");
  });

  it("returns no escalation for safe files", () => {
    const result = checkAutoEscalation(["src/utils/helper.ts"], POLICY.auto_escalation);
    expect(result.escalated).toBe(false);
    expect(result.matchedFiles).toHaveLength(0);
  });

  it("deduplicates matched files", () => {
    const result = checkAutoEscalation(
      ["src/auth/login.ts", "src/auth/logout.ts"],
      POLICY.auto_escalation,
    );
    expect(result.escalated).toBe(true);
    expect(result.matchedFiles).toHaveLength(2);
  });
});
