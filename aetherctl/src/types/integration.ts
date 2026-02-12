/** Integration record — documents how a change was merged into develop. (V3 §6.6) */
export type ConflictEntry = {
  file: string;
  resolution: "ours" | "theirs" | "manual";
  rationale: string;
};

export type RiskOverride = {
  overridden: boolean;
  original_level?: string | null;
  reason?: string | null;
  approved_by?: string | null;
};

export type ChangeType = "feature" | "bugfix" | "dependency" | "upstream" | "refactor";
export type RiskLevel = "low" | "medium" | "high";
export type MergeStrategy = "merge" | "rebase" | "cherry-pick";

export type Integration = {
  incubation_id: string;
  created_at: string;
  source_branch: string;
  target_branch: "develop";
  merge_strategy: MergeStrategy;
  conflicts?: ConflictEntry[];
  risk_level: RiskLevel;
  risk_override?: RiskOverride;
  change_type: ChangeType;
  files_changed: string[];
  lines_added: number;
  lines_removed: number;
};
