"""Run S01 scale-up against a disposable local Kind cluster."""

from pathlib import Path

from kind_scale_validation import ScaleScenario, run_scale_validation

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios" / "S01-scale-ui-up"

if __name__ == "__main__":
    raise SystemExit(run_scale_validation(ScaleScenario(
        scenario_id="S01",
        scenario_dir=SCENARIO_DIR,
        baseline_replicas=1,
        proposed_replicas=3,
    )))
