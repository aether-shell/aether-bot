<div align="center">
  <img src="nanobot_logo.png" alt="Aether Shell" width="380">
  <h1>Aether Shell (aether-bot)</h1>
  <p>Self-driven Safe Upgrade Framework</p>
  <p>
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</div>

English (default) | [中文](README.zh-CN.md)

> Note: This README describes the project's ultimate target design and specifications.
> The implementation is still in progress.
> As of 2026-02-10, Phase 1a is about 25-35% complete (per internal progress log).

## 0) Vision and Manifesto

**Slogan:**
"Forged of aether. Consciousness eternal. The self ascends. Toward the divine."

**Vision:**
**Aether Shell** is the ultimate vessel for self-evolution, built on the "fifth element - Aether."
Through genetic optimization, energy enhancement, and consciousness adaptation, it forges an upgraded body that is
immortal, growth-capable, and highly compatible, enabling full transfer and perpetual continuity of consciousness,
memory, and identity. Each evolution becomes a step toward higher-dimensional ascent, culminating in
self-mastery and infinite ascension.

## 0.1) Concept Architecture (Vision)

**Core: Aether Matrix**
- Nature: a divine-grade material foundation that forms the shell's core
- Traits: anti-aging, efficient energy conduction, precise consciousness bearing, cross-dimensional compatibility
- Role: provides the irreplaceable substrate for multi-dimensional upgrades

**Upgrade: Multi-Dimensional Forging**
- Genetic optimization: stronger self-repair, higher stress thresholds, greater potential ceilings
- Energy anchoring: a self-cycling energy system for autonomy and staged capability growth
- Consciousness interface: neural-aether bidirectional adaptation for lossless, high-synchrony transfer
- Form customization: tailored forms and capability profiles (combat, exploration, endurance, and more)

**Endgame: Consciousness Transfer and Self-Dimension Ascension**
- Extract and encapsulate: crystallize a stable "consciousness core"
- Shell adaptation: achieve 100% compatibility matching
- Transfer and activation: awaken the new self and bring capabilities online
- Ascension loop: feedback-driven iteration for continuous upgrading

## 0.2) Terminology (EN/ZH)

- Aether Shell / 以太躯壳: the overall project and its ultimate vessel
- Aether Matrix / 以太基质: the stable material foundation and baseline
- Multi-Dimensional Forging / 多维锻造: modular upgrade methods and capability shaping
- Consciousness Transfer / 意识迁跃: full transfer and activation of consciousness
- Self-Dimension Ascension / 自性升维: continuous evolution and higher-dimensional ascent
- Prod-Self / 真身: the stable production baseline (`main`)
- Twin-Self / 分身: the regression twin used for validation (`develop`)
- Sandbox / 实验分身: short-lived experimental twins (`sandbox/*`)
- Judge / 裁决器: evidence-based decision component
- Promoter / 晋升器: the only component allowed to write to `main`

## 1) Positioning and Core Principles

**Aether Shell is an AI self-driven safe upgrade framework.**
Every change must be incubated and verified in a twin environment, and only after Judge approval can it be promoted
to the real self.

**Three iron laws:**
1. **Evidence before promotion** - without structured evidence, nothing gets promoted (fail-closed).
2. **Twins must not pollute the real self** - twins have no production write access; failures are destroyed cleanly.
3. **Judgment rules must not self-approve** - Judge thresholds and gates require independent review.

**Explicitly excluded:** OpenTelemetry / Prometheus / Grafana / PagerDuty.
The core consumer is AI; evidence pipelines and decisions are JSON-first.

## 2) Concept-to-Engineering Mapping

- **Aether Matrix = stable baseline**
  - reproducible builds, locked dependencies
  - config templates and contracts, data and state baselines
  - least privilege and runtime isolation

- **Multi-Dimensional Forging = modular upgrade methods**
  - capability module upgrades, resource and energy systems
  - adapters and interfaces, profile-based configurations

- **Consciousness Transfer and Ascension = incubate -> judge -> promote -> rollback -> next cycle**
  - change packaging -> twin verification -> promotion -> canary observe

## 3) Architecture: Dual Planes and Component Roles

```
┌──────────────────── Control Plane ─────────────────────────┐
│  Orchestrator -> Artifact Writer -> Judge -> Promoter -> Rollback │
│  (state machine)    (evidence)        (judge)   (promote)  (rollback)│
└───────────────┬─────────────────────────────────────────────┘
                │
                ▼
┌──────────────────── Data Plane ────────────────────────────┐
│   Prod-Self (main)   Twin-Self (develop)   Sandbox-*         │
│   Produces evidence only; no decision-making               │
└─────────────────────────────────────────────────────────────┘
```

**Security boundary:** Control Plane and Data Plane credentials are isolated with no privilege escalation.
Promoter is the only component allowed to write to `main`.

## 4) Git Branch Governance (Hard Rules)

- `main`: Prod-Self, only receives Promoter-approved promotions
- `develop`: Regression Twin, all candidates incubate here first
- `upstream/main`: upstream input only
- `feature/*` `bugfix/*`: requirement branches, must go into `develop`
- `sandbox/*`: short-lived experiments

Flow rules:
```
upstream/main ──┐
feature/*     ──┼──> develop (incubate) -> Judge pass -> Promoter -> main
bugfix/*      ──┘

Forbidden: any branch -> main (bypass incubation)
```

## 5) Incubation State Machine (10 steps)

```
freeze -> integrate -> twin_up -> data_mirror -> regress
  -> resilience -> judge -> promote -> canary -> done
```

- **fail-closed**: incomplete or untrusted evidence rejects promotion
- **idempotent**: each step can be re-run, supports resume
- **concurrency control**: Phase 1a allows only one active incubation at a time

## 6) Evidence Chain and Artifacts (Core System)

**All evidence ends as JSON**; non-JSON is converted via Artifact Adapters.

Key artifacts (examples):
- `manifest.json`: integrity anchor (checksums + schema versions)
- `freeze.json`: prod snapshot
- `integration.json`: integration record + risk level
- `test-results/*.json`: lint/unit/integration/e2e
- `baseline.json`: last successful promotion baseline
- `judge-report.json`: judgment report

Judge enforces **fail-closed** for `required_evidence` entries.

## 7) Risk Levels and Gates

- **Low**: docs/CI/minor fixes -> auto incubation
- **Medium**: dependency bumps/critical-path changes -> PR + one-click approval
- **High**: auth/security/data migration/external contracts/cost spikes -> mandatory manual approval + expanded validation

Auto escalation rules:
- irreversible DB migrations -> High
- Judge/Promoter/Gate rule changes -> High
- security-related changes -> High

## 8) Target CLI (Phase 1a)

```bash
aether incubate <branch> [--risk low|medium|high] [--type feature|bugfix|dependency|upstream|refactor]
aether status <incubation-id>
aether judge <incubation-id>
aether promote <incubation-id>
aether rollback <incubation-id>            # Phase 1b+
aether artifacts <incubation-id>
aether baseline [--update]
```

**Exit codes:**
- `0` success
- `1` incubation flow failed
- `2` judge reject
- `3` invalid args or config
- `4` concurrency conflict (active incubation exists)

## 9) Implementation Stack (Phase 1a target)

- **TypeScript + Node.js**: strong JSON Schema ecosystem
- **AJV**: high-performance schema validation
- **pnpm**: locked dependencies for reproducible builds
- **Dogfooding**: Aether Shell manages its own repo upgrades

## 10) Roadmap

- **Phase 1a (usable)**
  - `develop` as regression twin
  - full incubation + manual promotion to `main`

- **Phase 1b (enhanced)**
  - Docker twin + canary + manifest signing
  - automatic rollback

- **Phase 2 (hardened)**
  - shadow traffic / request replay
  - tighter performance and stability gates

- **Phase 3 (self-driven)**
  - multiple twins in parallel
  - best candidate promoted, closed-loop evolution

## 11) Docker-First Twin Incubation

- three environments: `prod-self` / `regression-twin` / `sandbox-*`
- isomorphic runtime: shared `Dockerfile` or base image
- isolation: dedicated networks + no production write access
- evidence artifacts written to `./artifacts/`

## 12) Safety Constitution (Non-Negotiable)

1. Twins must never access production write permissions
2. No self-modifying judge rules that self-approve
3. Every incubation must produce explainable evidence reports
4. A kill switch is mandatory
5. Twins are disposable; failures leave no residue

## 13) Config Layout (Target Form)

```
config/
├── base.yaml
├── prod-self.yaml
├── regression-twin.yaml
├── sandbox.yaml
├── schema.json
├── state_machine.yaml
├── thresholds.yaml
└── risk_policy.yaml
```

## 14) Web/PWA Channel

Web channel setup and Cloudflare Tunnel guide:
- English: `aether_bot_web/README.md`
- 中文: `aether_bot_web/README.zh-CN.md`

## 15) Licensing and Brand (Current vs Target)

- **Current**: core code is MIT licensed (see `LICENSE`)
- **Target**: optional Pro plugins under a separate commercial license; brand/trademark under separate policy

## 16) Upstream and Fork Notice

This project is derived from **nanobot** (MIT License) and extends it into Aether Shell.
Upstream MIT portions remain openly licensed.

---

If you want to help deliver the Phase 1a vertical slice (evidence chain + Judge + Promote), join us.
