import type { RiskPolicyConfig, RiskRule } from "../types/config.js";
import type { RiskLevel } from "../types/integration.js";
import { minimatch } from "minimatch";

/**
 * Match a file path against risk rules and return the matching level.
 * Returns null if no rule matches.
 */
export function matchPathRule(filePath: string, rules: RiskRule[]): RiskLevel | null {
  for (const rule of rules) {
    if (minimatch(filePath, rule.pattern)) {
      return rule.level;
    }
  }
  return null;
}

/**
 * Get all risk rules from config, sorted by severity (high first).
 */
export function sortRulesBySeverity(rules: RiskRule[]): RiskRule[] {
  const order: Record<string, number> = { high: 0, medium: 1, low: 2 };
  return [...rules].sort((a, b) => (order[a.level] ?? 99) - (order[b.level] ?? 99));
}

/**
 * Load risk rules from a RiskPolicyConfig.
 */
export function getRiskRules(policy: RiskPolicyConfig): {
  pathRules: RiskRule[];
  autoEscalation: string[];
} {
  return {
    pathRules: policy.path_rules ?? [],
    autoEscalation: policy.auto_escalation ?? [],
  };
}
