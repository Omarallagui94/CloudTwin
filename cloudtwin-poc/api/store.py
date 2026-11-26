"""In-process state store for the CloudTwin API (Task H8).

Keeps loaded snapshots and completed simulations in memory so that
multiple API calls within a session can reference them by ID without
re-reading files.  This is intentionally simple and local-first;
production would swap this for a database-backed repository.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional


class APIStore:
    """Thread-safe in-memory store for snapshots and simulations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[str, dict[str, Any]] = {}   # snapshot_id -> twin dict
        self._simulations: dict[str, dict[str, Any]] = {}  # simulation_id -> result dict

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def save_snapshot(self, snapshot_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._snapshots[snapshot_id] = data

    def get_snapshot(self, snapshot_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._snapshots.get(snapshot_id)

    def list_snapshots(self) -> list[str]:
        with self._lock:
            return sorted(self._snapshots.keys())

    def snapshot_exists(self, snapshot_id: str) -> bool:
        with self._lock:
            return snapshot_id in self._snapshots

    # ------------------------------------------------------------------
    # Simulations (Future Twins)
    # ------------------------------------------------------------------

    def save_simulation(self, simulation_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._simulations[simulation_id] = data

    def get_simulation(self, simulation_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._simulations.get(simulation_id)

    def list_simulations(self) -> list[str]:
        with self._lock:
            return sorted(self._simulations.keys())

    def simulation_exists(self, simulation_id: str) -> bool:
        with self._lock:
            return simulation_id in self._simulations


# Module-level singleton shared across the FastAPI app
_store = APIStore()


def get_store() -> APIStore:
    """Return the global in-process API store."""
    return _store
