from visualization.build_graph import build_graph, icon_name, legend_items, project_diff, project_twin, readable_label


def _twin():
    resources = [
        {"id": "aws:internet", "name": "Internet", "type": "INTERNET", "provider": "aws", "properties": {}},
        {"id": "aws:load-balancer:demo", "name": "demo-public-nlb", "type": "LOAD_BALANCER", "provider": "aws", "properties": {"scheme": "internet-facing"}},
        {"id": "aws:vpc:vpc-demo", "name": "retail-vpc", "type": "VPC", "provider": "aws", "properties": {}},
        {"id": "aws:subnet:subnet-demo", "name": "public-a", "type": "SUBNET", "provider": "aws", "properties": {}},
        {"id": "aws:eks:retail-store", "name": "retail-store", "type": "EKS_CLUSTER", "provider": "aws", "properties": {}},
        {"id": "aws:ec2:i-demo", "name": "worker-demo", "type": "EC2_INSTANCE", "provider": "aws", "properties": {"instance_type": "t3.small"}},
        {"id": "k8s:service:default:ui", "name": "ui", "type": "SERVICE", "provider": "kubernetes", "properties": {"namespace": "default", "service_type": "LoadBalancer"}},
        {"id": "k8s:deployment:default:ui", "name": "ui", "type": "DEPLOYMENT", "provider": "kubernetes", "properties": {"namespace": "default", "replicas": 1}},
        {"id": "k8s:pod:default:ui-abc", "name": "ui-abc", "type": "POD", "provider": "kubernetes", "properties": {"namespace": "default"}},
        {"id": "k8s:node:worker", "name": "worker", "type": "NODE", "provider": "kubernetes", "properties": {}},
        {"id": "k8s:service:kube-system:metrics", "name": "metrics", "type": "SERVICE", "provider": "kubernetes", "properties": {"namespace": "kube-system"}},
    ]
    relationships = [
        {"source": "aws:internet", "target": "aws:load-balancer:demo", "type": "EXPOSES"},
        {"source": "aws:load-balancer:demo", "target": "k8s:service:default:ui", "type": "ROUTES_TO"},
        {"source": "k8s:service:default:ui", "target": "k8s:deployment:default:ui", "type": "EXPOSES"},
        {"source": "k8s:deployment:default:ui", "target": "k8s:pod:default:ui-abc", "type": "SELECTS"},
        {"source": "k8s:pod:default:ui-abc", "target": "k8s:node:worker", "type": "RUNS_ON"},
        {"source": "aws:vpc:vpc-demo", "target": "aws:subnet:subnet-demo", "type": "CONTAINS"},
        {"source": "aws:eks:retail-store", "target": "aws:vpc:vpc-demo", "type": "LOCATED_IN"},
        {"source": "aws:ec2:i-demo", "target": "aws:subnet:subnet-demo", "type": "LOCATED_IN"},
    ]
    return {"schema_version": "1.0.0", "resources": resources, "relationships": relationships}


def test_application_view_keeps_only_actual_connected_application_edges():
    projection = project_twin(_twin(), "Application View")
    ids = {resource["id"] for resource in projection["resources"]}
    assert ids == {
        "aws:internet", "aws:load-balancer:demo",
        "k8s:service:default:ui", "k8s:deployment:default:ui",
    }
    assert len(projection["relationships"]) == 3
    assert "k8s:service:kube-system:metrics" not in ids


def test_all_graph_views_are_projections_and_full_view_is_complete(tmp_path):
    twin = _twin()
    expected_types = {
        "Application View": {"INTERNET", "LOAD_BALANCER", "SERVICE", "DEPLOYMENT"},
        "Kubernetes View": {"SERVICE", "DEPLOYMENT", "POD", "NODE"},
        "AWS Infrastructure View": {"INTERNET", "LOAD_BALANCER", "VPC", "SUBNET", "EKS_CLUSTER", "EC2_INSTANCE"},
    }
    for view, types in expected_types.items():
        projection = project_twin(twin, view)
        assert {resource["type"] for resource in projection["resources"]} == types
        output = build_graph(projection, tmp_path / f"{view}.html", view=view)
        generated = output.read_text(encoding="utf-8")
        assert "selectNode" in generated
        assert "hierarchical" in generated
        assert "data:image/svg+xml;base64" in generated
        assert "Resource details" in generated
        assert "Fit view" in generated
    full = project_twin(twin, "Full Infrastructure")
    assert len(full["resources"]) == len(twin["resources"])
    assert len(full["relationships"]) == len(twin["relationships"])
    assert build_graph(full, tmp_path / "full.html", view="Full Infrastructure").is_file()


def test_labels_are_short_and_human_readable():
    service = {"id": "k8s:service:default:orders", "name": "orders", "type": "SERVICE"}
    deployment = {"id": "k8s:deployment:default:ui", "name": "ui", "type": "DEPLOYMENT"}
    assert readable_label(service) == "Orders Service"
    assert readable_label(deployment) == "UI"


def test_application_and_infrastructure_icon_mapping_is_semantic_and_self_contained():
    assert icon_name({"name": "ui", "type": "DEPLOYMENT"}) == "monitor"
    assert icon_name({"name": "catalog", "type": "SERVICE"}) == "package"
    assert icon_name({"name": "carts", "type": "SERVICE"}) == "cart"
    assert icon_name({"name": "orders-postgresql", "type": "STATEFUL_SET"}) == "database"
    assert icon_name({"name": "checkout-redis", "type": "STATEFUL_SET"}) == "lightning"
    assert icon_name({"name": "orders-rabbitmq", "type": "STATEFUL_SET"}) == "message"
    assert icon_name({"name": "retail-store", "type": "EKS_CLUSTER"}) == "kubernetes"
    items = legend_items()
    assert [label for label, _color, _icon in items] == [
        "Network", "Kubernetes", "Application", "Database / Messaging", "AWS Infrastructure", "Security"
    ]
    assert all(icon.startswith("data:image/svg+xml;base64,") for _label, _color, icon in items)


def test_diff_projection_uses_only_actual_removed_relationships_and_real_context(tmp_path):
    current = _twin()
    removed_id = "k8s:service:default:ui"
    future = {
        **current,
        "resources": [item for item in current["resources"] if item["id"] != removed_id],
        "relationships": [
            item for item in current["relationships"]
            if item["source"] != removed_id and item["target"] != removed_id
        ],
    }
    removed_relationships = [
        item for item in current["relationships"]
        if item["source"] == removed_id or item["target"] == removed_id
    ]
    diff = {
        "changes": [{"resource_id": removed_id, "change_type": "REMOVED"}],
        "added_relationships": [],
        "removed_relationships": removed_relationships,
    }
    projection = project_diff(current, future, diff)
    assert projection["relationships"] == [
        {**item, "_diff_state": "removed"}
        for item in sorted(removed_relationships, key=lambda rel: (rel["source"], rel["target"], rel["type"]))
    ]
    removed = next(item for item in projection["resources"] if item["id"] == removed_id)
    assert removed["_diff_state"] == "removed"
    generated = build_graph(projection, tmp_path / "diff.html", view="Diff View").read_text(encoding="utf-8")
    assert '"label": "\\u2715 UI Service"' in generated
    assert '"dashes": true' in generated
