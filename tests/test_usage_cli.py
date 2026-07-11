from scripts.usage import main


def test_usage_cli_summary(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.usage.usage_store.query_summary",
        lambda **kwargs: {"owner": kwargs["owner"], "totals": {"runs": 0}},
    )
    assert main(["--owner", "alice", "summary"]) == 0
    assert '"owner": "alice"' in capsys.readouterr().out


def test_usage_cli_delete_requires_scoped_choice():
    try:
        main(["delete"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("delete accepted without an explicit scope")
