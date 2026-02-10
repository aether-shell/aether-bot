import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { validateAll, type Diagnostic } from "./validate.js";

export type StepStatus = "pending" | "running" | "ok" | "error";

export type RunStep = {
  id: string;
  status: StepStatus;
  startedAt?: string;
  finishedAt?: string;
  error?: { message: string; code?: string };
  diagnostics?: Diagnostic[];
  outputs?: Record<string, unknown>;
};

export type RunState = {
  runId: string;
  createdAt: string;
  cwd: string;
  configPath: string;
  steps: RunStep[];
  version?: number;
};

export type RunResult =
  | { ok: true; runId: string; statePath: string }
  | { ok: false; runId?: string; statePath?: string; error: { code: string; message: string } };

export const EXIT_CODES = {
  OK: 0,
  INPUT_INVALID: 2,
  RETRYABLE_FAILURE: 10,
  PERMANENT_FAILURE: 20
} as const;

function nowIso(): string {
  return new Date().toISOString();
}

function randId(bytes = 8): string {
  return crypto.randomBytes(bytes).toString("hex");
}

export function makeRunId(): string {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  return `${ts}-${randId(6)}`;
}

export function defaultRunsRoot(cwd: string): string {
  return path.join(cwd, ".aether", "runs");
}

export function statePathForRun(runsRoot: string, runId: string): string {
  return path.join(runsRoot, runId, "state.json");
}

export function loadState(statePath: string): RunState {
  const raw = fs.readFileSync(statePath, "utf8");
  return JSON.parse(raw) as RunState;
}

export function saveState(statePath: string, state: RunState): void {
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2) + "\n", "utf8");
}

export async function run(opts: {
  configDir: string;
  runId?: string;
  runsRoot?: string;
  format?: "human" | "jsonl";
  until?: string;
  from?: string;
}): Promise<RunResult> {
  const cwd = process.cwd();
  const runsRoot = opts.runsRoot ? path.resolve(cwd, opts.runsRoot) : defaultRunsRoot(cwd);
  const configPath = path.resolve(cwd, opts.configDir);
  const format = opts.format ?? "human";

  if (!fs.existsSync(configPath) || !fs.statSync(configPath).isDirectory()) {
    return {
      ok: false,
      error: {
        code: "CONFIG_DIR_MISSING",
        message: `Config directory not found: ${configPath}`
      }
    };
  }


  const runId = opts.runId ?? makeRunId();
  const statePath = statePathForRun(runsRoot, runId);

  let state: RunState;
  if (fs.existsSync(statePath)) {
    state = loadState(statePath);
  } else {
    state = {
      version: 1,
      runId,
      createdAt: nowIso(),
      cwd,
      configPath: path.relative(cwd, configPath),
      steps: [
        { id: "validate", status: "pending" },
        { id: "plan", status: "pending" }
      ]
    };
    saveState(statePath, state);
  }

  function stepIndex(id: string): number {
    return state.steps.findIndex((s) => s.id === id);
  }

  const fromIndex = opts.from ? stepIndex(opts.from) : 0;
  const untilIndex = opts.until ? stepIndex(opts.until) : state.steps.length - 1;
  const rangeValid = fromIndex !== -1 && untilIndex !== -1 && fromIndex <= untilIndex;

  if (!rangeValid) {
    return {
      ok: false,
      runId,
      statePath,
      error: {
        code: "STEP_RANGE_INVALID",
        message: `Invalid step range: from=${opts.from ?? "(start)"} until=${opts.until ?? "(end)"}`
      }
    };
  }

  async function runStep(
    stepId: string,
    fn: () => Promise<
      | { ok: true; diagnostics?: Diagnostic[]; outputs?: Record<string, unknown> }
      | { ok: false; code: string; message: string; diagnostics?: Diagnostic[] }
    >
  ): Promise<RunResult | null> {
    const idx = stepIndex(stepId);
    const step = idx === -1 ? undefined : state.steps[idx];
    if (!step) return null;

    if (idx < fromIndex || idx > untilIndex) return null;
    if (step.status === "ok") return null;

    step.status = "running";
    step.startedAt = nowIso();
    step.finishedAt = undefined;
    step.error = undefined;
    saveState(statePath, state);

    const res = await fn();
    if (!res.ok) {
      step.status = "error";
      step.finishedAt = nowIso();
      step.error = { code: res.code, message: res.message };
      step.diagnostics = res.diagnostics ?? [];
      saveState(statePath, state);

      if (format === "jsonl") {
        for (const d of step.diagnostics ?? []) process.stdout.write(JSON.stringify(d) + "\n");
      }

      return { ok: false, runId, statePath, error: { code: res.code, message: res.message } };
    }

    step.status = "ok";
    step.finishedAt = nowIso();
    step.diagnostics = res.diagnostics ?? [{ level: "info", code: "OK", message: "OK" }];
    step.outputs = res.outputs;
    saveState(statePath, state);

    if (format === "jsonl") {
      process.stdout.write(JSON.stringify({ level: "info", code: `${stepId.toUpperCase()}_OK`, message: `${stepId} OK` }) + "\n");
    }

    return null;
  }

  const validateFail = await runStep("validate", async () => {
    const vres = await validateAll({ configDir: configPath });
    if (!vres.ok) return { ok: false, code: "VALIDATE_FAILED", message: "Validation failed", diagnostics: vres.errors };
    return { ok: true, diagnostics: [{ level: "info", code: "OK", message: "OK" }] };
  });
  if (validateFail) return validateFail;

  const planFail = await runStep("plan", async () => {
    const planPath = path.join(path.dirname(statePath), "plan.json");
    const plan = {
      runId,
      generatedAt: nowIso(),
      steps: state.steps.map((s) => ({ id: s.id, status: s.status }))
    };
    fs.writeFileSync(planPath, JSON.stringify(plan, null, 2) + "\n", "utf8");

    return {
      ok: true,
      diagnostics: [{ level: "info", code: "PLAN_WRITTEN", message: "Wrote plan.json" }],
      outputs: { planPath: path.relative(cwd, planPath) }
    };
  });
  if (planFail) return planFail;

  return { ok: true, runId, statePath };
}
