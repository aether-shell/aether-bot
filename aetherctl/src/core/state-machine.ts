import type { StateMachineConfig, Phase } from "../types/config.js";
import type { RiskLevel } from "../types/integration.js";

/**
 * All possible incubation steps in order.
 */
export const ALL_STEPS = [
  "freeze",
  "integrate",
  "twin_up",
  "data_mirror",
  "regress",
  "resilience",
  "judge",
  "promote",
  "canary",
] as const;

export type IncubationStep = (typeof ALL_STEPS)[number];

/**
 * Terminal and error states.
 */
export type IncubationStatus =
  | IncubationStep
  | "done"
  | "rejected"
  | `failed_${IncubationStep}`
  | `timeout_${IncubationStep}`;

/**
 * Events that drive state transitions.
 */
export type TransitionEvent = "success" | "failure" | "timeout" | "rejected";

/**
 * Default skip rules for Phase 1a (§4.3).
 */
const DEFAULT_SKIP_1A: string[] = ["twin_up", "data_mirror", "resilience", "canary", "rollback"];

/**
 * Determine the effective step list for a given phase, applying skip rules.
 */
export function getEffectiveSteps(phase: Phase, config?: StateMachineConfig): IncubationStep[] {
  const steps = (config?.steps ?? [...ALL_STEPS]) as IncubationStep[];
  const skipRules = config?.skip_rules ?? { "1a": DEFAULT_SKIP_1A };
  const toSkip = new Set(skipRules[phase] ?? []);
  return steps.filter((s) => !toSkip.has(s));
}

/**
 * Determine whether a step should be skipped for the given phase and risk level.
 */
export function shouldSkip(step: string, phase: Phase, risk: RiskLevel, config?: StateMachineConfig): boolean {
  const effective = getEffectiveSteps(phase, config);
  if (!effective.includes(step as IncubationStep)) return true;

  // Phase 1b: resilience can be skipped for low risk (§4.3)
  if (phase === "1b" && step === "resilience" && risk === "low") return true;

  return false;
}

/**
 * Pure function: given current step + event, return next state.
 */
export function nextState(
  current: IncubationStep,
  event: TransitionEvent,
  phase: Phase,
  config?: StateMachineConfig,
): IncubationStatus {
  if (event === "failure") return `failed_${current}`;
  if (event === "timeout") return `timeout_${current}`;
  if (event === "rejected") return "rejected";

  // success → advance to next non-skipped step
  const effective = getEffectiveSteps(phase, config);
  const idx = effective.indexOf(current);
  if (idx === -1) return `failed_${current}`;
  if (idx >= effective.length - 1) return "done";
  return effective[idx + 1];
}

/**
 * Get the timeout for a step in seconds.
 */
export function getStepTimeout(step: IncubationStep, config?: StateMachineConfig): number {
  const defaults: Record<string, number> = {
    freeze: 60, integrate: 120, twin_up: 300, data_mirror: 300,
    regress: 600, resilience: 600, judge: 60, promote: 120, canary: 600, done: 10,
  };
  return config?.timeouts?.[step] ?? defaults[step] ?? 120;
}
