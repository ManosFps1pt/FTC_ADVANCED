# Paper MCP quick guide

## Why it can work in one task but not another

Paper is connected **per Codex task session**. A Paper browser tab by itself
does not guarantee that its MCP tools are available in every task. Before
trying to edit a canvas, confirm both of these things:

1. The task exposes Paper tools (their names begin with `mcp__paper__`).
2. A Paper file is open and `get_basic_info` returns its file, page, and
   artboard information.

If either is missing, do not claim the edit succeeded. Refresh/reopen the
Paper tab, then start or reload the affected Codex task so it can acquire the
Paper connection. If it still lacks the Paper tools, the integration is not
attached to that task session and needs to be reconnected there.

## Safe editing workflow

1. Call `get_guide({ topic: "paper-mcp-instructions" })` once per task
   session. Re-read it after a very long task if the instructions may have
   been compacted.
2. Call `get_basic_info` to identify the active file, page, artboards, and
   existing design tokens.
3. Call `get_selection` to understand the user's focus. For existing content,
   inspect only what is needed with `get_tree_summary`, `get_children`,
   `get_node_info`, or `get_screenshot`.
4. Before creating a new visual, post a brief: mood, palette, typography, and
   direction. For an edit, state the intended visual change first.
5. Make small changes. Use `write_html` for one visual group at a time;
   prefer `duplicate_nodes`, `set_text_content`, `update_styles`, and
   `move_nodes` when modifying existing nodes.
6. Call `get_font_family_info` before the first typographic styling in a
   session. Use `px` for font sizes and line heights, and `em` for letter
   spacing when possible.
7. After meaningful changes, call `get_screenshot` and check spacing,
   typography, contrast, alignment, repetition, and clipping. Fix any issue
   found before declaring the work done.
8. Finish with `finish_working_on_nodes`.

## Minimal connection test

Run the following in order:

```text
get_guide({ topic: "paper-mcp-instructions" })
get_basic_info({})
get_selection({})
```

Success means the last two calls return information from the currently open
Paper document. At that point, it is safe to edit the canvas.

## Avoid

- Do not assume an open tab means the MCP server is connected to this task.
- Do not edit blindly without inspecting the current document and a screenshot.
- Do not send a large, all-at-once HTML layout; build incrementally.
- Do not expose Paper node IDs in user-facing messages.
