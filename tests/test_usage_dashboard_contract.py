from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_usage_workspace_exposes_all_dashboard_suites_and_accessible_tables():
    html = (ROOT / "static/index.html").read_text()
    script = (ROOT / "static/js/usageDashboard.js").read_text()

    for view in ("overview", "runs", "cache", "activity", "cost", "anomalies"):
        assert f'id="usage-{view}"' in html
    assert 'class="usage-data-table"' in script
    assert "aria: { enabled: true" in script
    assert "/api/usage/anomalies" in script
    assert "/api/usage/runs/" in script


def test_usage_dashboard_filters_are_shared_by_charts_tables_and_permalink():
    script = (ROOT / "static/js/usageDashboard.js").read_text()
    assert "const filterValues" in script
    assert "usage_filter" not in script  # URL contract uses usage_<dimension>.
    assert "url.searchParams.set(`usage_${key}`" in script
    assert "event.name; persistView(); refresh()" in script
