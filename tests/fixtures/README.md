# Test fixtures — SYNTHETIC

Every `*.jsonl` file here is **synthetic**. None is a copy of a real Claude Code
transcript. The line-level schema (field names, `type` values, `message.content`
item shapes, `usage` keys) mirrors the real format so the parser is exercised
faithfully, but all *content* is fabricated: fake session UUIDs, fake cwds
(`/home/dev/example`), invented tool calls, and placeholder text. No real paths,
usernames, tokens, or message bodies appear.

| file | scenario |
|------|----------|
| `clean_session.jsonl`     | a session that starts, uses a couple of tools, and ends cleanly (→ `done`) |
| `blocked_session.jsonl`   | a session that hits a permission/notification prompt (→ `blocked`, with a question) |
| `tool_heavy_session.jsonl`| a tool-dense session (many edits/tests, token accumulation) |
