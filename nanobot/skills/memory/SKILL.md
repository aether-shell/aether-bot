---
name: memory
description: Two-layer memory system with grep-based recall.
metadata: {"nanobot":{"emoji":"🧠","aliases":["remember","memory-store"],"triggers":["memory","remember","history","long-term memory","记忆","记住","历史记录","长期记忆"],"allowed_tools":["read_file","write_file","edit_file","exec"]}}
always: true
---

# Memory

## Structure

- `memory/MEMORY.md` — Long-term facts (preferences, project context, relationships). Always loaded into your context.
- `memory/HISTORY.md` — Append-only event log. NOT loaded into context. Search it with grep.

## Search Past Events

```bash
grep -i "keyword" memory/HISTORY.md
```

Use the `exec` tool to run grep. Combine patterns: `grep -iE "meeting|deadline" memory/HISTORY.md`

## When to Update MEMORY.md

Write important facts immediately using `edit_file` or `write_file`:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

## Auto-consolidation

Old conversations are automatically summarized and appended to HISTORY.md when the session grows large. Long-term facts are extracted to MEMORY.md. You don't need to manage this.

## Knowledge Capture

After performing substantial web research (multiple web_search + web_fetch calls), evaluate whether findings are worth persisting as learned knowledge in memory/learnings/.

**Decision levels:**
1. **Auto-save**: Verified, reusable knowledge (API signatures, patterns, gotchas). Save silently.
2. **Ask user**: Potentially valuable but uncertain. Ask: "Save this to knowledge base?"
3. **Skip**: Routine, task-specific, or already known. Do nothing.

**Guidelines:**
- Prefer saving generalizable knowledge over task-specific debugging output.
- Check if a learning on this topic already exists in memory/learnings/ — update rather than duplicate.
- Use the learn skill's file format (YAML frontmatter + standard sections).
