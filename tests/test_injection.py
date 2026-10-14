"""The project root and the clients can be replaced (used by the offline `spg run` test). No network or API calls."""

import httpx
from typer.testing import CliRunner

from success_plan import cli
from success_plan.paths import PROJECT_ROOT, CompanyPaths, project_root
from success_plan.stages.retrieve.http import RateLimitedClient


def test_project_root_defaults_to_the_repository_and_follows_spg_root(monkeypatch, tmp_path):
    monkeypatch.delenv("SPG_ROOT", raising=False)
    assert project_root() == PROJECT_ROOT and CompanyPaths.for_company("Test Co").root == PROJECT_ROOT
    monkeypatch.setenv("SPG_ROOT", str(tmp_path))
    assert project_root() == tmp_path and CompanyPaths.for_company("Test Co").data == tmp_path / "data" / "test-co"
    assert CompanyPaths.for_company("Test Co", root=PROJECT_ROOT).root == PROJECT_ROOT  # an explicit root wins


def test_cli_builds_its_http_client_through_the_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("SPG_ROOT", str(tmp_path))
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")
    seen = []

    def fake_client(user_agent, on_backoff=None):
        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={"0": {"cik_str": 1, "ticker": "OTHR", "title": "Other Co"}})
        return RateLimitedClient(user_agent, transport=httpx.MockTransport(handler), sleep=lambda s: None)

    monkeypatch.setattr(cli, "make_http_client", fake_client)
    result = CliRunner().invoke(cli.app, ["retrieve", "--company", "Test Co"])
    assert result.exit_code == 1 and "No SEC registrant matches" in result.output
    assert seen and seen[0].startswith("https://www.sec.gov/files/company_tickers.json")
    assert not (tmp_path / "data").exists() or not any((tmp_path / "data").rglob("01_retrieve.json"))
