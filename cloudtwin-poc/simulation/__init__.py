"""Simulation : Future Twin et Diff (pôle Développement et graphe, tâches H5 et H6)."""

from simulation.diff import changed_resource_ids, diff_twins, removed_relationships
from simulation.future_twin import FutureTwin, SimulationError, build_future_twin, simulate_future_twin
from simulation.state_diff import StateDiff, calculate_state_diff
from simulation.affected_resources import AffectedResourcesResult, analyze_affected_resources

__all__ = [
    "AffectedResourcesResult", "FutureTwin", "SimulationError", "StateDiff",
    "analyze_affected_resources", "build_future_twin", "calculate_state_diff",
    "changed_resource_ids", "diff_twins", "removed_relationships", "simulate_future_twin",
]
