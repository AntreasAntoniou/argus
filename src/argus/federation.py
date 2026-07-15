"""Federation — full-mesh state exchange across machines.

STUB — precise typed contract only. Implementers: implement ``DESIGN.md``
decision #6 (Full mesh). Every node runs an identical ``argusd``; the static
peer list lives in :attr:`argus.config.ArgusConfig.peers`. Peers (a) receive
immediate event pushes and (b) exchange full local :class:`FleetState` on an
interval. Any node merges remote state (tagged by machine) into its own
:class:`FleetState` to render the whole fleet. At N≈4 this is symmetric state
exchange over ``httpx`` — deliberately boring, not a gossip protocol.
"""

from __future__ import annotations

from argus.config import ArgusConfig
from argus.models import Event, FleetState


class Federation:
    """Manages peer connections and fleet-state merge for one node."""

    def __init__(self, config: ArgusConfig) -> None:
        """Initialise with the static peer list from config.

        Args:
            config: This node's config; ``config.peers`` is the ``host:port``
                mesh and ``config.machine`` is this node's tag.
        """

        raise NotImplementedError("Store config, init httpx client + peer endpoints")

    async def push_event(self, event: Event) -> None:
        """Fan an event out to every peer immediately (fire-and-forget).

        Failures to individual peers are logged, never raised — a down peer must
        not stall local ingestion.
        """

        raise NotImplementedError("POST event to each peer's /peer/event endpoint")

    async def exchange(self, local: FleetState) -> FleetState:
        """Run one round of full-state exchange and return the merged fleet view.

        POSTs ``local`` to each peer's state endpoint, collects their
        :class:`FleetState`, and merges all machines into one view via
        :meth:`argus.models.FleetState.merge`.

        Args:
            local: This node's current single-or-multi-machine fleet state.

        Returns:
            A merged :class:`FleetState` spanning self + all reachable peers.
        """

        raise NotImplementedError("Exchange full state with peers, merge, return")

    async def run(self, get_local: object, interval_seconds: float = 2.0) -> None:
        """Run the periodic exchange loop until cancelled.

        Args:
            get_local: A zero-arg callable returning the current local
                :class:`FleetState` (typically ``store.local_fleet``).
            interval_seconds: Seconds between exchange rounds.
        """

        raise NotImplementedError("Periodic exchange loop at interval_seconds")

    async def merge_remote(self, remote: FleetState) -> None:
        """Merge a peer-pushed :class:`FleetState` into the local aggregate view."""

        raise NotImplementedError("Merge remote FleetState into aggregate")
