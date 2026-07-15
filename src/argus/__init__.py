"""Argus — the hundred-eyed watchman for your coding-agent fleet.

Public surface re-exported here so downstream code imports from ``argus``
directly. The domain model (:mod:`argus.models`) and configuration
(:mod:`argus.config`) are the shared contract; everything else builds on them.
"""

from __future__ import annotations

from argus.config import (
    ARGUS_HOME,
    DEFAULT_CONFIG_PATH,
    ArgusConfig,
    NotifierConfig,
    NotifierKind,
    Paths,
    Thresholds,
    load_config,
)
from argus.models import (
    Buckets,
    Event,
    FleetState,
    HookEvent,
    SessionSnapshot,
    SessionStatus,
    TimelineEntry,
    TimelineKind,
    utcnow,
)

__version__ = "0.1.0"

__all__ = [
    # models
    "Buckets",
    "Event",
    "FleetState",
    "HookEvent",
    "SessionSnapshot",
    "SessionStatus",
    "TimelineEntry",
    "TimelineKind",
    "utcnow",
    # config
    "ARGUS_HOME",
    "DEFAULT_CONFIG_PATH",
    "ArgusConfig",
    "NotifierConfig",
    "NotifierKind",
    "Paths",
    "Thresholds",
    "load_config",
    "__version__",
]
