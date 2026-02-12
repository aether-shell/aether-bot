import fs from "node:fs";
import path from "node:path";
import type { AetherConfig, Phase } from "../types/config.js";
import type { RiskLevel } from "../types/integration.js";
import type { Baseline } from "../types/baseline.js";
import {
  type IncubationStep,
  type IncubationStatus,
  getEffectiveSteps,
  nextState,
  getStepTimeout,
} from "./state-machine.js";
import { checkConcurrency } from "./concurrency.js";
import { BaselineManager } from "../judge/baseline-manager.js";

/** Persistent incubation state stored in artifacts/{id}/state.json */
export type IncubationState = {
  incubation_id: string;
  phase: Phase;
  current_step: IncubationStatus;
  source_branch: string;
  change_type: string;
  risk_level: RiskLevel;
  started_at: string;
  updated_at: string;
  step_started_at: string | null;
  step_results: Record<string, { status: string; duration_ms: number; error?: string }>;
  error: string | null;
};

export type OrchestratorResult = {
  success: boolean;
  incubation_id: string;
  final_status: IncubationStatus;
  step_results: IncubationState["step_results"];
  error?: string;
};

export type StepRunner = (
  step: IncubationStep,
  state: IncubationState,
  config: AetherConfig,
) => Promise<{ success: boolean; error?: string }>;

/**
 * Orchestrator — drives the incubation pipeline through the state machine.
 *
 * Main loop: load state → execute step → persist → advance.
 * Supports resume from last state on restart.
 */
export class Orchestrator {
  private readonly artifactsDir: string;
  private readonly config: AetherConfig;
  private readonly stepRunner: StepRunner;

  constructor(artifactsDir: string, config: AetherConfig, stepRunner: StepRunner) {
    this.artifactsDir = artifactsDir;
    this.config = config;
    this.stepRunner = stepRunner;
  }

  /**
   * Start or resume an incubation.
   */
  async run(opts: {
    incubationId: string;
    sourceBranch: string;
    changeType: string;
    riskLevel: RiskLevel;
    forceRestart?: boolean;
  }): Promise<OrchestratorResult> {
    const phase = this.config.phase;
    const stateDir = path.join(this.artifactsDir, opts.incubationId);
    const statePath = path.join(stateDir, "state.json");

    // Concurrency check
    const concurrency = checkConcurrency(
      this.artifactsDir,
      this.config.state_machine?.max_concurrent ?? 1,
    );
    if (!concurrency.allowed) {
      return {
        success: false,
        incubation_id: opts.incubationId,
        final_status: "failed_freeze",
        step_results: {},
        error: concurrency.reason,
      };
    }

    // Load or create state
    let state: IncubationState;
    if (!opts.forceRestart && fs.existsSync(statePath)) {
      state = JSON.parse(fs.readFileSync(statePath, "utf8"));
    } else {
      fs.mkdirSync(stateDir, { recursive: true });
      const effectiveSteps = getEffectiveSteps(phase, this.config.state_machine);
      state = {
        incubation_id: opts.incubationId,
        phase,
        current_step: effectiveSteps[0],
        source_branch: opts.sourceBranch,
        change_type: opts.changeType,
        risk_level: opts.riskLevel,
        started_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        step_started_at: null,
        step_results: {},
        error: null,
      };
      this.saveState(statePath, state);
    }

    // Check if already in terminal state
    if (this.isTerminal(state.current_step)) {
      return {
        success: state.current_step === "done",
        incubation_id: opts.incubationId,
        final_status: state.current_step,
        step_results: state.step_results,
      };
    }

    // Main loop
    const effectiveSteps = getEffectiveSteps(phase, this.config.state_machine);

    while (!this.isTerminal(state.current_step)) {
      const step = state.current_step as IncubationStep;
      if (!effectiveSteps.includes(step)) {
        // Skip this step — should not happen with proper state machine, but be safe
        state.current_step = nextState(step, "success", phase, this.config.state_machine);
        state.updated_at = new Date().toISOString();
        this.saveState(statePath, state);
        continue;
      }

      const timeoutMs = getStepTimeout(step, this.config.state_machine) * 1000;
      state.step_started_at = new Date().toISOString();
      state.updated_at = new Date().toISOString();
      this.saveState(statePath, state);

      const stepStart = Date.now();

      try {
        const result = await Promise.race([
          this.stepRunner(step, state, this.config),
          this.timeoutPromise(timeoutMs, step),
        ]);

        const duration_ms = Date.now() - stepStart;

        if (result.success) {
          state.step_results[step] = { status: "success", duration_ms };
          state.current_step = nextState(step, "success", phase, this.config.state_machine);
        } else if (result.error === "TIMEOUT") {
          state.step_results[step] = { status: "timeout", duration_ms, error: result.error };
          state.current_step = nextState(step, "timeout", phase, this.config.state_machine);
          state.error = `Timeout at step ${step}`;
        } else if (result.error === "REJECTED") {
          state.step_results[step] = { status: "rejected", duration_ms, error: result.error };
          state.current_step = "rejected";
          state.error = `Rejected at step ${step}`;
        } else {
          state.step_results[step] = { status: "failed", duration_ms, error: result.error };
          state.current_step = nextState(step, "failure", phase, this.config.state_machine);
          state.error = result.error ?? `Failed at step ${step}`;
        }
      } catch (e: any) {
        const duration_ms = Date.now() - stepStart;
        const errorMsg = e?.message ?? String(e);
        state.step_results[step] = { status: "failed", duration_ms, error: errorMsg };
        state.current_step = nextState(step, "failure", phase, this.config.state_machine);
        state.error = errorMsg;
      }

      state.step_started_at = null;
      state.updated_at = new Date().toISOString();
      this.saveState(statePath, state);
    }

    return {
      success: state.current_step === "done",
      incubation_id: opts.incubationId,
      final_status: state.current_step,
      step_results: state.step_results,
      error: state.error ?? undefined,
    };
  }

  private isTerminal(status: IncubationStatus): boolean {
    return status === "done" || status === "rejected" || status.startsWith("failed_") || status.startsWith("timeout_");
  }

  private saveState(statePath: string, state: IncubationState): void {
    fs.writeFileSync(statePath, JSON.stringify(state, null, 2), "utf8");
  }

  private timeoutPromise(ms: number, step: string): Promise<{ success: false; error: string }> {
    return new Promise((resolve) => {
      setTimeout(() => resolve({ success: false, error: "TIMEOUT" }), ms);
    });
  }
}
