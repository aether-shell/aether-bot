#!/usr/bin/env node

import { Command } from "commander";
import { validateAll } from "./commands/validate.js";
import { run, EXIT_CODES } from "./commands/run.js";
import { openspecApproveStart, openspecCancel, openspecResume, openspecStatus } from "./commands/openspec.js";
import { incubate } from "./commands/incubate.js";
import { status, listIncubations } from "./commands/status.js";
import { listArtifacts } from "./commands/artifacts.js";
import { readBaseline, updateBaseline } from "./commands/baseline.js";
import { EXIT } from "./commands/exit-codes.js";

const program = new Command();

program
  .name("aetherctl")
  .description("Aether Shell control-plane CLI")
  .version("0.1.0");

program
  .command("validate")
  .description("Validate config and (optionally) artifacts")
  .option("--config <path>", "Path to config directory", "config")
  .option("--artifacts <path>", "Path to artifacts directory")
  .option("--format <format>", "Output format: human|jsonl", "human")
  .action(
    async (opts: { config: string; artifacts?: string; format: "human" | "jsonl" }) => {
      const res = await validateAll({
        configDir: opts.config,
        artifactsDir: opts.artifacts
      });

      if (!res.ok) {
        if (opts.format === "jsonl") {
          for (const err of res.errors) {
            process.stdout.write(JSON.stringify(err) + "\n");
          }
        } else {
          for (const err of res.errors) console.error(err.message);
        }
        process.exit(1);
      }

      if (opts.format === "jsonl") {
        process.stdout.write(JSON.stringify({ level: "info", code: "OK", message: "OK" }) + "\n");
      } else {
        console.log("OK");
      }
    }
  );

program
  .command("run")
  .description("Run aether pipeline with checkpointing")
  .option("--config <path>", "Path to config directory", "config")
  .option("--run <id>", "Resume an existing run id")
  .option("--runs-root <path>", "Runs root directory (default: .aether/runs)")
  .option("--from <step>", "Start from step id (skip earlier steps even if pending)")
  .option("--until <step>", "Run until step id (inclusive)")
  .option("--format <format>", "Output format: human|jsonl", "human")
  .action(
    async (opts: { config: string; run?: string; runsRoot?: string; from?: string; until?: string; format: "human" | "jsonl" }) => {
      const res = await run({
        configDir: opts.config,
        runId: opts.run,
        runsRoot: opts.runsRoot,
        from: opts.from,
        until: opts.until,
        format: opts.format
      });

      if (!res.ok) {
        const exit = res.error.code === "VALIDATE_FAILED" ? EXIT_CODES.RETRYABLE_FAILURE : EXIT_CODES.INPUT_INVALID;
        if (opts.format === "jsonl") {
          if (res.runId && res.statePath) {
            process.stdout.write(
              JSON.stringify({ level: "error", code: res.error.code, message: res.error.message, runId: res.runId, statePath: res.statePath }) +
                "\n"
            );
          } else {
            process.stdout.write(JSON.stringify({ level: "error", code: res.error.code, message: res.error.message }) + "\n");
          }
        } else {
          console.error(res.error.message);
        }
        process.exit(exit);
      }

      if (opts.format === "jsonl") {
        process.stdout.write(JSON.stringify({ level: "info", code: "OK", runId: res.runId, statePath: res.statePath }) + "\n");
      } else {
        process.stdout.write(JSON.stringify({ runId: res.runId, statePath: res.statePath }) + "\n");
      }
    }
  );

// --- Phase 1a incubation commands ---

program
  .command("incubate")
  .description("Start or resume an incubation pipeline")
  .argument("<branch>", "Source branch to incubate")
  .option("--config <path>", "Path to config directory", "config")
  .option("--risk <level>", "Risk level: low|medium|high", "low")
  .option("--type <type>", "Change type: feature|bugfix|refactor|dependency", "feature")
  .option("--force-restart", "Force restart from scratch")
  .option("--dry-run", "Dry run (skip actual promote)")
  .option("--format <format>", "Output format: human|jsonl", "human")
  .action(
    async (branch: string, opts: { config: string; risk: string; type: string; forceRestart?: boolean; dryRun?: boolean; format: "human" | "jsonl" }) => {
      const res = await incubate({
        branch,
        configDir: opts.config,
        risk: opts.risk as any,
        type: opts.type as any,
        forceRestart: opts.forceRestart,
        dryRun: opts.dryRun,
        format: opts.format,
      });

      if (!res.ok) {
        if (opts.format === "jsonl") {
          process.stdout.write(JSON.stringify({ level: "error", error: res.error }) + "\n");
        } else {
          console.error(res.error);
        }
        process.exit(res.exitCode);
      }

      if (opts.format === "jsonl") {
        process.stdout.write(JSON.stringify({ level: "info", incubation_id: res.incubationId, status: res.status }) + "\n");
      } else {
        console.log(`Incubation ${res.incubationId}: ${res.status}`);
      }
    }
  );

program
  .command("status")
  .description("Show incubation status")
  .argument("[id]", "Incubation ID (omit to list all)")
  .option("--artifacts-dir <path>", "Artifacts directory", ".aether/artifacts")
  .option("--format <format>", "Output format: human|jsonl", "human")
  .action(
    (id: string | undefined, opts: { artifactsDir: string; format: "human" | "jsonl" }) => {
      if (id) {
        const res = status({ artifactsDir: opts.artifactsDir, incubationId: id });
        if (!res.ok) {
          if (opts.format === "jsonl") {
            process.stdout.write(JSON.stringify({ level: "error", error: res.error }) + "\n");
          } else {
            console.error(res.error);
          }
          process.exit(1);
        }
        if (opts.format === "jsonl") {
          process.stdout.write(JSON.stringify(res.state) + "\n");
        } else {
          console.log(JSON.stringify(res.state, null, 2));
        }
      } else {
        const list = listIncubations(opts.artifactsDir);
        if (opts.format === "jsonl") {
          for (const item of list) process.stdout.write(JSON.stringify(item) + "\n");
        } else {
          if (list.length === 0) { console.log("No incubations found."); return; }
          for (const item of list) console.log(`${item.id}  ${item.status}  ${item.updated_at}`);
        }
      }
    }
  );

program
  .command("artifacts")
  .description("List artifacts for an incubation")
  .argument("<id>", "Incubation ID")
  .option("--artifacts-dir <path>", "Artifacts directory", ".aether/artifacts")
  .option("--format <format>", "Output format: human|jsonl", "human")
  .action(
    (id: string, opts: { artifactsDir: string; format: "human" | "jsonl" }) => {
      const res = listArtifacts({ artifactsDir: opts.artifactsDir, incubationId: id });
      if (!res.ok) {
        if (opts.format === "jsonl") {
          process.stdout.write(JSON.stringify({ level: "error", error: res.error }) + "\n");
        } else {
          console.error(res.error);
        }
        process.exit(1);
      }
      if (opts.format === "jsonl") {
        for (const f of res.files) process.stdout.write(JSON.stringify(f) + "\n");
      } else {
        for (const f of res.files) console.log(`${f.path}  ${f.size} bytes`);
      }
    }
  );

program
  .command("baseline")
  .description("View or update baseline")
  .option("--artifacts-dir <path>", "Artifacts directory", ".aether/artifacts")
  .option("--update", "Update baseline from latest results")
  .option("--sha <sha>", "Main SHA for baseline update")
  .option("--format <format>", "Output format: human|jsonl", "human")
  .action(
    (opts: { artifactsDir: string; update?: boolean; sha?: string; format: "human" | "jsonl" }) => {
      if (opts.update) {
        if (!opts.sha) {
          console.error("--sha is required when using --update");
          process.exit(EXIT.INVALID_ARGS);
        }
        const res = updateBaseline(opts.artifactsDir, opts.sha);
        if (!res.ok) { console.error(res.error); process.exit(1); }
        if (opts.format === "jsonl") {
          process.stdout.write(JSON.stringify(res.baseline) + "\n");
        } else {
          console.log("Baseline updated.");
        }
      } else {
        const res = readBaseline(opts.artifactsDir);
        if (!res.ok) { console.error(res.error); process.exit(1); }
        if (opts.format === "jsonl") {
          process.stdout.write(JSON.stringify(res.baseline) + "\n");
        } else {
          console.log(JSON.stringify(res.baseline, null, 2));
        }
      }
    }
  );

// --- OpenSpec task-mode commands ---

const openspec = program.command("openspec").description("OpenSpec task-mode control plane");

openspec
  .command("resume")
  .description("Resume a paused change after providing required input")
  .requiredOption("--repo <path>", "Path to repo")
  .requiredOption("--change <id>", "OpenSpec change id")
  .requiredOption("--input <text>", "User input to unblock")
  .option("--by <actor>", "Requested by")
  .action(async (opts: { repo: string; change: string; input: string; by?: string }) => {
    await openspecResume({ repoPath: opts.repo, changeId: opts.change, userInput: opts.input, requestedBy: opts.by ?? null });
  });

openspec
  .command("approve_start")
  .description("Approve a planned change and enqueue it for autonomous execution")
  .requiredOption("--repo <path>", "Path to repo")
  .requiredOption("--change <id>", "OpenSpec change id")
  .option("--by <actor>", "Requested by")
  .action(async (opts: { repo: string; change: string; by?: string }) => {
    await openspecApproveStart({ repoPath: opts.repo, changeId: opts.change, requestedBy: opts.by ?? null });
  });

openspec
  .command("cancel")
  .description("Cancel a change (idempotent)")
  .requiredOption("--repo <path>", "Path to repo")
  .requiredOption("--change <id>", "OpenSpec change id")
  .action(async (opts: { repo: string; change: string }) => {
    await openspecCancel({ repoPath: opts.repo, changeId: opts.change });
  });

openspec
  .command("status")
  .description("Get current state for a change")
  .requiredOption("--repo <path>", "Path to repo")
  .requiredOption("--change <id>", "OpenSpec change id")
  .action(async (opts: { repo: string; change: string }) => {
    await openspecStatus({ repoPath: opts.repo, changeId: opts.change });
  });

program.parseAsync(process.argv).catch((err: unknown) => {
  const message = err instanceof Error ? err.message : String(err);
  process.stderr.write(JSON.stringify({ ok: false, error: message }) + "\n");
  process.exit(1);
});
