"""Ingestion paths — the three ways Argus sees sessions (no wrapper).

Per ``DESIGN.md`` decision #1 (Hybrid state source):

- :mod:`argus.ingest.hooks` — Claude Code hooks (real-time POST /hook).
- :mod:`argus.ingest.transcripts` — ``~/.claude/projects/**/*.jsonl`` watcher
  (backfill / catch-all for sessions started without hooks).
- :mod:`argus.ingest.tmux` — tmux poll (liveness, death detection, reply channel).

Each path produces :class:`argus.models.Event` objects fed to the reducer/store.
"""

from __future__ import annotations
