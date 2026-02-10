#!/usr/bin/env node

import { Command } from "commander";
import { validateAll } from "./commands/validate.js";
import { run, EXIT_CODES } from "./commands/run.js";

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

program.parseAsync(process.argv);
