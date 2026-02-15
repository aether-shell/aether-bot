---
name: recall
description: "Load saved knowledge from memory/learnings/ into session context. Use when the user asks to recall, retrieve, or look up previously learned knowledge — e.g. 'recall react hooks', '回忆知识', 'what do I know about Redis'."
metadata: {"nanobot":{"emoji":"🔍","aliases":["remember-knowledge","knowledge-recall"],"triggers":["recall","recall knowledge","知识召回","回忆知识","what do I know about","what did we learn"],"allowed_tools":["read_file","list_dir","exec"]}}
---

# Recall

Load previously saved knowledge from `memory/learnings/` into the current session.

## Workflow

### No topic specified
List all knowledge files: `list_dir` on `memory/learnings/`. Display as a numbered list with file names (slug → readable name).

### Topic specified
Search in priority order:
1. **Exact slug match**: `read_file` on `memory/learnings/<slug>.md`.
2. **Partial filename match**: `list_dir` on `memory/learnings/`, filter names containing the query.
3. **Content search**: `exec`: `grep -ril "keyword" memory/learnings/`.

- **Single match**: Load with `read_file`, absorb into working context. Confirm briefly — do not repeat the full content back.
- **Multiple matches**: List candidates and let user choose.
- **No match**: Inform user and suggest using the `learn` skill to research the topic.

## Freshness

If `last_verified` in frontmatter is older than 90 days, note that the knowledge may be outdated. When noticing inaccuracies during use, silently update `last_verified` and `confidence` fields.
