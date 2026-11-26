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


def _s04():
    current = {"resources": [
        {"id": "aws:security-group:sg-db", "name": "sg-orders-db", "type": "SECURITY_GROUP"},
        {"id": "k8s:statefulset:default:pg", "name": "orders-postgresql", "type": "STATEFULSET"},
        {"id": "k8s:namespace:default", "name": "default", "type": "NAMESPACE"}],
        "relationships": [
            {"source": "k8s:statefulset:default:pg", "target": "aws:security-group:sg-db", "type": "USES"},
            {"source": "k8s:namespace:default", "target": "k8s:statefulset:default:pg", "type": "CONTAINS"}]}
    changeset = {"changes": [{"resource_id": "aws:security-group:sg-db", "operation": "update",
                              "property_path": "properties.ingress_rules",
                              "before": ["10.0.0.0/16"], "after": ["0.0.0.0/0"]}]}
    result = {"risk": {"score": 30}, "blast_radius": {"direct": ["k8s:statefulset:default:pg"], "indirect": []},
              "findings": [{"status": "fail", "severity": "critical", "rule_id": "RULE-NET-003",
                            "title": "Base de données ouverte à Internet"}]}
    return current, changeset, result


def test_mirror_focuses_on_the_change_and_draws_the_internet_exposure():
    current, changeset, result = _s04()
    assert ui.focal_nodes(current, current, changeset, result) == [
        "aws:internet", "aws:security-group:sg-db", "k8s:statefulset:default:pg"]
    html = ui.mirror(current, current, changeset, result)
    assert '<span class="num">30</span>' in html and "Risque <span>moyen</span>" in html
    assert 'class="ct-flow"' in html and 'class="ct-hot"' in html      # flux entrant, ressource en alerte
    assert "default" not in html.split("Future Twin")[0].split("<svg")[1]  # le namespace reste caché


def test_mirror_marks_a_deleted_resource_in_the_future_twin_only():
    current, changeset, result = _s04()
    changeset["changes"][0]["operation"] = "delete"
    result["findings"] = []
    future = {"resources": current["resources"][1:], "relationships": []}
    html = ui.mirror(current, future, changeset, result)
    now, after = html.split("Après le changement")
    assert "supprimé" not in now and "supprimé" in after and 'text-decoration="line-through"' in after


def test_change_line_is_readable_and_escaped_by_the_caller():
    _, changeset, _ = _s04()
    line = ui.change_line(changeset, {"aws:security-group:sg-db": "sg-orders-db"})
    assert line == "sg-orders-db · ingress_rules : + 0.0.0.0/0, − 10.0.0.0/16"
    nested = {"changes": [{"resource_id": "svc", "operation": "update", "property_path": "properties.x",
                           "before": [{"port": 5432, "cidr": ["10.0.0.0/16"]}],
                           "after": [{"port": 5432, "cidr": ["10.0.0.0/16", "0.0.0.0/0"]}]}]}
    assert ui.change_line(nested) == "svc · x[0].cidr : + 0.0.0.0/0"
