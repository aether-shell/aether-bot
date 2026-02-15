---
name: learn
description: "Research a topic and save structured knowledge to memory/learnings/ for future reference. Use when the user asks to learn about, study, or research a topic for persistent storage — e.g. 'learn about React hooks', 'study Go concurrency', '学习 Redis Streams'."
metadata: {"nanobot":{"emoji":"📚","aliases":["study","research-topic"],"triggers":["learn","study","research","learn about","研究","学习","学一下","了解一下"],"allowed_tools":["web_search","web_fetch","read_file","write_file","edit_file","list_dir","exec"]}}
---

# Learn

Research a topic using web search and save structured knowledge for future recall.

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

5. **Save** with `write_file` to `memory/learnings/<slug>.md` using the format below.

6. **Confirm**: Report file path, source count, and 2-3 key takeaways.

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
