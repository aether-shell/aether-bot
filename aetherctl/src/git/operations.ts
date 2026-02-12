import { simpleGit, type SimpleGit } from "simple-git";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export type DiffStats = {
  files_changed: string[];
  lines_added: number;
  lines_removed: number;
};

export type MergeStrategy = "merge" | "rebase" | "cherry-pick";

/**
 * Git operations wrapper — abstracts simple-git for testability.
 */
export class GitOperations {
  private git: SimpleGit;

  constructor(repoPath: string, git?: SimpleGit) {
    this.git = git ?? simpleGit(repoPath);
  }

  /** Get HEAD SHA of a branch (defaults to current HEAD). */
  async getCurrentSha(branch?: string): Promise<string> {
    const ref = branch ?? "HEAD";
    const result = await this.git.revparse([ref]);
    return result.trim();
  }

  /** Compute SHA256 hash of the lockfile. */
  async getLockfileHash(repoPath: string, lockfileName = "pnpm-lock.yaml"): Promise<string> {
    const lockPath = path.join(repoPath, lockfileName);
    const content = fs.readFileSync(lockPath, "utf8");
    return createHash("sha256").update(content).digest("hex");
  }

  /** Merge a source branch into a target branch using the given strategy. */
  async mergeBranch(source: string, target: string, strategy: MergeStrategy): Promise<void> {
    await this.git.checkout(target);
    switch (strategy) {
      case "merge":
        await this.git.merge([source, "--no-ff"]);
        break;
      case "rebase":
        await this.git.rebase([source]);
        break;
      case "cherry-pick":
        // Cherry-pick the tip commit of source
        const sha = await this.getCurrentSha(source);
        await this.git.raw(["cherry-pick", sha]);
        break;
    }
  }

  /** Get list of changed files between two refs. */
  async getChangedFiles(base: string, head: string): Promise<string[]> {
    const diff = await this.git.diff(["--name-only", `${base}...${head}`]);
    return diff
      .trim()
      .split("\n")
      .filter((f) => f.length > 0);
  }

  /** Get diff statistics (files, lines added/removed) between two refs. */
  async getDiffStats(base: string, head: string): Promise<DiffStats> {
    const summary = await this.git.diffSummary([`${base}...${head}`]);
    return {
      files_changed: summary.files.map((f) => f.file),
      lines_added: summary.insertions,
      lines_removed: summary.deletions,
    };
  }

  /** Create an annotated tag. */
  async createTag(name: string, message?: string): Promise<void> {
    await this.git.tag(["-a", name, "-m", message ?? name]);
  }

  /** Merge a specific SHA into main (used by Promoter). */
  async mergeToMain(sha: string): Promise<void> {
    await this.git.checkout("main");
    await this.git.merge([sha, "--no-ff"]);
  }

  /** Get current branch name. */
  async getCurrentBranch(): Promise<string> {
    const result = await this.git.revparse(["--abbrev-ref", "HEAD"]);
    return result.trim();
  }
}
