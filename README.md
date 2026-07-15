# Argus

**The hundred-eyed watchman for your coding-agent fleet.**

Argus turns N parallel Claude Code sessions — across every machine you own — into
one glanceable board that answers the only question that matters:
**which agent needs me right now?**

```
┌ ARGUS ──────────────────────────────── 7 eyes open ┐
│ ⚠ NEEDS YOU (2)                                    │
│ ▸ hermes-fix   astrape  “Run db migration? (y/n)”  │
│ ▸ ogma-eval    forge    “Push to main?”            │
│ ── working ────────────────────────────────────────│
│ ● synthetes-ui  mac     editing   3m   +214/-80    │
│ ● athina-marks  mac     testing   41s              │
│ ── quiet ──────────────────────────────────────────│
│ ✓ argus-docs   done 12m        ☠ hz-sync dead 40m  │
└────────────────────────────────────────────────────┘
```

## Why

Agents work in bursts: thirty seconds of thinking, a flurry of edits, then waiting
on *you*. Past 3–4 parallel agents, raw terminal panes make **you** the dashboard.
Argus inverts that:

- **State, not text** — every session gets an explicit state machine
  (`thinking / editing / testing / blocked / done / dead`), including dead-agent
  detection tmux will never give you.
- **Interrupt queue** — blocked agents float to the top with their exact question;
  answer `y`/`n`/typed replies inline (guarded injection: Argus verifies the prompt
  is still on-screen before sending keys). Enter attaches to the real pane.
- **No gravity** — Argus *observes* sessions however they were launched
  (tmux, bare `claude`, SSH boxes). There is no mandatory wrapper.
- **Fleet-wide** — a full mesh of `argusd` daemons; any machine renders the whole
  fleet, and a batched WhatsApp digest reaches you when you're away.
- **Drill-down** — per-agent semantic timeline (tools, files, tests, tokens) and
  read-only diff of whatever branch the session is actually on.

## How it sees

Three ingestion paths, no wrapper:

1. **Claude Code hooks** (async POST) — millisecond-fresh lifecycle events.
2. **Transcript watcher** — tails `~/.claude/projects/**/*.jsonl`; catches sessions
   started without hooks, backfills timelines.
3. **tmux poll** — liveness, death detection, and the guarded-reply channel.

## Status

Early. Design record in [DESIGN.md](DESIGN.md). Python 3.14 + FastAPI + Textual.

## License

MIT
