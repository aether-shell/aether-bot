---
name: recall
description: "Load saved knowledge from memory/learnings/ into session context. Use when the user asks to recall, retrieve, or look up previously learned knowledge — e.g. 'recall react hooks', '回忆知识', 'what do I know about Redis'."
metadata: {"nanobot":{"emoji":"🔍","aliases":["remember-knowledge","knowledge-recall"],"triggers":["recall","recall knowledge","knowledge recap","review what we learned","what do I know about","what did we learn","what have we learned","what do we know","recap what we learned","give me a refresher on","tell me about what we learned","walk me through what we learned","知识召回","回忆知识","回顾","复习","回顾一下","复习一下","之前学过","我们学过的","刚学到的","把之前学到的讲一下","讲一讲","讲讲","讲一下","说说","说一下","聊聊","介绍一下","科普一下","展开讲讲","再讲一遍","复盘一下"],"allowed_tools":["read_file","list_dir","exec"]}}
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
