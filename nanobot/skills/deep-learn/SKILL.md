---
name: deep-learn
description: "Deep multi-agent research on a topic. Spawn parallel subagents to cover different dimensions, then synthesize into comprehensive knowledge. Use when the user asks for deep research, thorough study, or multi-angle investigation — e.g. 'deep learn kubernetes networking', '深度研究 React 性能优化'."
metadata: {"nanobot":{"emoji":"🔬","aliases":["deep-research","multi-agent-learn"],"triggers":["deep learn","deep research","深度研究","深入研究","深入学习","全面研究","deep dive"],"allowed_tools":["web_search","web_fetch","read_file","write_file","edit_file","list_dir","exec","spawn"]}}
---

# Deep Learn

Conduct deep, multi-dimensional research on a topic using parallel subagents.

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

3. **Ensure directories**: `exec`: `mkdir -p memory/learnings/.tmp`

4. **Spawn subagents** — one `spawn` per dimension with this task template:
   ```
   Research "<topic>": <dimension focus>.
   Use web_search and web_fetch to gather authoritative information.
   Write findings to memory/learnings/.tmp/<dimension-slug>.md in markdown.
   Include: key concepts, code examples, gotchas, and source URLs.
   ```
   Subagents write to `memory/learnings/.tmp/<dimension-slug>.md`. They return automatically via MessageBus when done.

5. **Synthesize** — after all subagents complete:
   - Read all `.tmp/*.md` files.
   - De-duplicate overlapping content.
   - Cross-validate facts across dimensions.
   - Organize into the standard knowledge file format (see below).
   - Add an **Advanced Topics** section covering cross-cutting concerns.

6. **Save** with `write_file` to `memory/learnings/<slug>.md`. Extra frontmatter fields:
   ```yaml
   research_depth: "deep"
   agents_used: N
   strategy: "subagents"
   ```

7. **Clean up**: `exec`: `rm -rf memory/learnings/.tmp`

8. **Report**: Dimensions covered, agents used, total sources, 3-5 key findings as bullet points.

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
