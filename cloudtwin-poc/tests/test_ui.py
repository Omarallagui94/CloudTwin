"""Composants d'interface : seuils de niveau, bande de verdict, piste du pipeline."""

from visualization import ui


def test_levels_follow_the_scoring_thresholds():
    assert [ui.level_of(s)[1] for s in (0, 29, 30, 59, 60, 79, 80, 100)] == [
        "Faible", "Faible", "Moyen", "Moyen", "Élevé", "Élevé", "Critique", "Critique"]


def test_verdict_strip_places_the_marker_and_names_the_worst_risk():
    findings = [
        {"status": "fail", "severity": "high", "title": "Security Group ouvert à Internet"},
        {"status": "fail", "severity": "critical", "title": "Base de données ouverte à Internet"},
        {"status": "pass", "severity": "high", "title": "SSH"},
    ]
    html = ui.verdict_strip({"score": 30}, findings)
    assert 'style="left:30%"' in html
    assert "Risque <span>moyen</span>" in html
    assert "2 risques actifs, le plus grave : Base de données ouverte à Internet" in html
    assert '<div class="medium on"' in html


def test_verdict_strip_says_when_the_score_is_a_minimum():
    html = ui.verdict_strip({"score": 0}, [{"status": "not_evaluated", "severity": "high", "title": "x"}])
    assert "Aucun risque actif" in html and "le score est un minimum" in html


def test_pipeline_track_marks_done_stages_and_escapes_names():
    html = ui.pipeline_track([("Current Twin", True), ("<Diff>", False)])
    assert '<div class="stage done" title="Current Twin : terminée">' in html
    assert "&lt;Diff&gt;" in html and "<Diff>" not in html
