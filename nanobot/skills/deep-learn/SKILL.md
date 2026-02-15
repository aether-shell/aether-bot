---
name: deep-learn
description: "Deep, multi-dimensional research with durable persistence. Prefer completing end-to-end in the current turn and writing final results to memory/learnings/. Use when the user asks for deep research, thorough study, or multi-angle investigation — e.g. 'deep learn kubernetes networking', '深度研究 React 性能优化'."
metadata: {"nanobot":{"emoji":"🔬","aliases":["deep-research","multi-agent-learn"],"triggers":["deep learn","deep research","深度研究","深入研究","深入学习","全面研究","deep dive"],"allowed_tools":["web_search","web_fetch","read_file","write_file","edit_file","spawn"],"workflow":{"kickoff":{"require_substantive_action":true,"substantive_tools":["web_search","web_fetch","write_file","spawn"],"forbid_as_first_only":["list_dir","exec"]},"completion":{"require_tool_calls":[{"name":"write_file","args":{"path_regex":"^memory/learnings/[^/]+\\.md$"}}]},"retry":{"enforcement_retries":1,"failure_mode":"explain_missing"},"progress":{"claim_requires_actions":true,"claim_patterns":["executing_now","开始做","开始执行","执行中","已完成","completed"],"milestones":{"enabled":true,"tool_call_interval":3,"max_messages":3,"templates":{"kickoff":"进度：已开始执行，正在检索权威资料。","researching":"进度：资料检索中，已获取 {source_calls} 个来源。","synthesizing":"进度：正在整理关键信息并归纳结论。","completion_ready":"进度：文档已保存，正在生成最终答复。"}}}}}}
---

# Deep Learn

Conduct deep, multi-dimensional research on a topic and persist the final synthesized result.

## Workflow

1. **Generate slug + check existing** — same as the learn skill. If exists, ask Update/Replace/Cancel.

2. **Analyze and plan dimensions**: Break the topic into 2-5 research dimensions based on its nature. Show the plan to the user.
   - Example for "kubernetes networking": CNI & network models, Service/Ingress/LB, NetworkPolicy & security, DNS & service discovery.
   - **Execution transparency (required):** the plan message must explicitly state:
     - `Execution status`: `waiting_for_confirmation` or `executing_now`
     - `Confirmation needed`: `yes` or `no`
     - `Next action`: one concrete next step
   - If confirmation is required, ask an explicit yes/no question at the end.
   - For normal deep-research requests (e.g. "deep research ...", "深入研究...", "继续"), default to `executing_now` and do **not** wait for another turn unless the user explicitly asks to review/edit the plan first.

2.5 **Kick off immediately when safe**:
   - If there is no update/replace conflict from step 1 and no destructive side effect, continue to step 3 in the same turn.
   - Avoid ambiguous intent wording ("I will continue later"). Clearly indicate whether execution has started.
   - If you claim `executing_now`, you must perform substantive tool actions in this same turn.
   - A plan-only response is invalid for `executing_now`.

3. **Substantive kickoff (mandatory)**:
   - The first substantive action must be `web_search`/`web_fetch` or `spawn` for a real research dimension.
   - Do not use directory listing or placeholder-only actions as kickoff.
   - Use `write_file` to create/update working notes in `memory/learnings/.tmp/` as research progresses (parent dirs are auto-created).

4. **Research dimensions (default: synchronous in current turn)**:
   - For each dimension, run focused `web_search` + `web_fetch`.
   - Write intermediate notes to `memory/learnings/.tmp/<dimension-slug>.md` with `write_file`.
   - Keep each dimension non-overlapping and source-backed.

5. **Optional parallel mode (only when explicitly requested or clearly beneficial)**:
   - Use `spawn` for dimension tasks only if the user asked for background/parallel execution, or the topic is too large for one turn.
   - If using `spawn`, still continue orchestration and **do not** claim completion until final synthesis is saved.
   - Subagent "completed successfully" announcements are intermediate signals, not final user completion.

6. **Synthesize**:
   - Read all relevant `.tmp/*.md` notes.
   - De-duplicate overlapping content.
   - Cross-validate facts across dimensions.
   - Organize into the standard knowledge file format (see below).
   - Add an **Advanced Topics** section covering cross-cutting concerns.

7. **Save (mandatory before completion)** with `write_file` to `memory/learnings/<slug>.md`. Extra frontmatter fields:
   ```yaml
   research_depth: "deep"
   agents_used: N
   strategy: "single-agent|subagents"
   ```

8. **Optional cleanup**:
   - Keep `.tmp` notes by default for traceability/reproducibility.
   - If you do cleanup by other means, never remove the final `memory/learnings/<slug>.md`.

9. **Report**: Dimensions covered, agents used, total sources, 3-5 key findings as bullet points, and the saved file path.

## File Format

Same as the learn skill format, plus:

```markdown
## Advanced Topics
Cross-cutting concerns, edge cases, and deep-dive material synthesized across dimensions.
```

## Quality Rules

- Each dimension should contribute unique, non-overlapping content.
- Conflicting information across dimensions must be noted and resolved.
- Mark low-confidence claims explicitly.
