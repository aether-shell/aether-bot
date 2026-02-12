import type { RiskPolicyConfig } from "../types/config.js";
import type { ChangeType, RiskLevel } from "../types/integration.js";
import { matchPathRule, sortRulesBySeverity, getRiskRules } from "./rules.js";
import { checkAutoEscalation } from "./auto-escalation.js";

export type ClassificationInput = {
  files_changed: string[];
  change_type: ChangeType;
  lines_added: number;
  lines_removed: number;
};

export type ClassificationResult = {
  level: RiskLevel;
  reason: string;
  auto_escalated: boolean;
};

const RISK_ORDER: Record<RiskLevel, number> = { low: 0, medium: 1, high: 2 };

function higherRisk(a: RiskLevel, b: RiskLevel): RiskLevel {
  return RISK_ORDER[a] >= RISK_ORDER[b] ? a : b;
}

/**
 * Classify risk level for a set of changed files.
 *
 * Algorithm:
 * 1. Check auto-escalation patterns first (→ high if matched)
 * 2. Match each file against path rules, take highest level
 * 3. Apply heuristics for large changes (>500 lines → medium bump)
 * 4. Default to "low" if no rules match
 */
export function classifyRisk(
  input: ClassificationInput,
  policy: RiskPolicyConfig,
): ClassificationResult {
  const { pathRules, autoEscalation } = getRiskRules(policy);
  const reasons: string[] = [];

  // Step 1: Auto-escalation check
  const escalation = checkAutoEscalation(input.files_changed, autoEscalation);
  if (escalation.escalated) {
    return {
      level: "high",
      reason: `Auto-escalated: files [${escalation.matchedFiles.join(", ")}] match pattern '${escalation.matchedPattern}'`,
      auto_escalated: true,
    };
  }

  // Step 2: Path rule matching — take highest severity
  let level: RiskLevel = "low";
  const sortedRules = sortRulesBySeverity(pathRules);

  for (const file of input.files_changed) {
    const matched = matchPathRule(file, sortedRules);
    if (matched) {
      const prev = level;
      level = higherRisk(level, matched);
      if (level !== prev) {
        reasons.push(`'${file}' → ${matched}`);
      }
    }
  }

  // Step 3: Large change heuristic
  const totalLines = input.lines_added + input.lines_removed;
  if (totalLines > 500 && RISK_ORDER[level] < RISK_ORDER["medium"]) {
    level = "medium";
    reasons.push(`Large change: ${totalLines} lines modified`);
  }

  // Step 4: Dependency change type bump
  if (input.change_type === "dependency" && RISK_ORDER[level] < RISK_ORDER["medium"]) {
    level = "medium";
    reasons.push("Dependency change type");
  }

  const reason = reasons.length > 0 ? reasons.join("; ") : "No risk rules matched, defaulting to low";

  return { level, reason, auto_escalated: false };
}
