---
name: forget
description: "Delete saved knowledge from memory/learnings/. Use when the user asks to forget, remove, or delete previously learned knowledge — e.g. 'forget react hooks', '删除知识'."
metadata: {"nanobot":{"emoji":"🗑️","aliases":["delete-knowledge","remove-knowledge"],"triggers":["forget knowledge","delete knowledge","remove knowledge","忘记知识","删除知识"],"allowed_tools":["read_file","list_dir","exec"]}}
---

# Forget

Delete knowledge files from `memory/learnings/`.

## Workflow

### No topic specified
`list_dir` on `memory/learnings/` — display all files and ask user which to delete.

### Topic specified
1. Search: exact slug match → partial filename match via `list_dir`.
2. **Confirm before deleting**: Show file path, topic name, and created date from frontmatter. Ask user to confirm.
3. Delete: `exec`: `rm memory/learnings/<slug>.md`.
4. Confirm deletion is complete.

Never delete without explicit user confirmation.
