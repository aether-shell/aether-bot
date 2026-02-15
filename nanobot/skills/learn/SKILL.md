---
name: learn
description: "Study a topic and save structured knowledge to memory/learnings/ for future reference. Use when the user asks to learn or study a topic for persistent storage — e.g. 'learn about React hooks', 'study Go concurrency', '学习 Redis Streams'."
metadata: {"nanobot":{"emoji":"📚","aliases":["study","research-topic"],"triggers":["learn","study","learn about","research this","study this topic","学习","学一下","学习一下","了解一下","研究一下","调研一下"],"allowed_tools":["web_search","web_fetch","read_file","write_file","edit_file","list_dir","exec"],"workflow":{"kickoff":{"require_substantive_action":true,"substantive_tools":["web_search","web_fetch","write_file"],"forbid_as_first_only":["list_dir"]},"completion":{"require_tool_calls":[{"name":"write_file","args":{"path_regex":"^memory/learnings/[^/]+\\.md$"}}]},"retry":{"enforcement_retries":1,"failure_mode":"explain_missing"},"progress":{"claim_requires_actions":true,"claim_patterns":["开始学习","开始研究","executing","已完成","completed"],"milestones":{"enabled":true,"tool_call_interval":2,"max_messages":2,"templates":{"kickoff":"进度：已开始执行，正在检索权威资料。","researching":"进度：资料检索中，已获取 {source_calls} 个来源。","completion_ready":"进度：文档已保存，正在生成最终答复。"}}}}}}
---

# Learn

Research a topic using web search and save structured knowledge for future recall.

Path rule: always use workspace-relative `memory/learnings/...` paths in tool calls.

## Workflow

1. **Generate slug** from the topic: lowercase, replace non-alphanumeric with hyphens, collapse consecutive hyphens, trim leading/trailing hyphens.

2. **Check existing**: `read_file` on `memory/learnings/<slug>.md`. If it exists, ask: Update (merge new info) / Replace (overwrite) / Cancel.

3. **Ensure directory**: `exec`: `mkdir -p memory/learnings`

4. **Research** — choose strategy by topic category:
   - **Library/Framework**: `web_search` for official docs + API reference, `web_fetch` key pages, `web_search` for known issues/gotchas.
   - **Concept/Pattern**: `web_search` for authoritative explanations, `web_fetch` 1-2 reference articles.
   - **Tool/CLI**: `web_search` for official docs + common recipes, `web_fetch` doc pages.
   - **Language Feature**: `web_search` for spec/reference + examples, `web_fetch` reference page.
   - Aim for 2-3 `web_search` + 2-3 `web_fetch` calls total.

5. **Save (mandatory before completion)** with `write_file` to `memory/learnings/<slug>.md` using the format below.

6. **Confirm**: Report file path, source count, and 2-3 key takeaways. Do not claim "completed" if step 5 has not succeeded.

## File Format

```markdown
---
topic: "Topic Name"
slug: "topic-name"
category: "library|concept|tool|language-feature"
created: "YYYY-MM-DD"
last_verified: "YYYY-MM-DD"
confidence: "high|medium|low"
tags: [tag1, tag2]
sources_count: N
---

# Topic Name

## TL;DR
2-4 sentence overview.

## Core APIs / Concepts
Key APIs, functions, or concepts with signatures and brief descriptions.
Include code examples where helpful.

## Patterns & Recipes
Common usage patterns as code blocks.

## Gotchas
- **Issue**: Description → **Fix**: Solution

## Quick Reference
Cheat-sheet style summary table or bullet list.

## Sources
1. [Title](URL)
```

## Quality Rules

- Accuracy over breadth — verify claims across sources.
- Include version numbers when relevant.
- Mark unverified code with `// untested`.
- When using a knowledge file later and finding outdated content, silently update `last_verified` and `confidence`.
