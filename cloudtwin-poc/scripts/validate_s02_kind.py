"""Run S02 scale-down against a disposable local Kind cluster."""

from pathlib import Path

from kind_scale_validation import ScaleScenario, run_scale_validation

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios" / "S02-scale-ui-down"

if __name__ == "__main__":
    raise SystemExit(run_scale_validation(ScaleScenario(
        scenario_id="S02",
        scenario_dir=SCENARIO_DIR,
        baseline_replicas=3,
        proposed_replicas=1,
    )))
