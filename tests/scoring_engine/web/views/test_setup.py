"""
Tests for the web setup wizard:
  - GET /setup
  - POST /api/setup
  - helper functions: _parse_bracket_form, _build_config, _validate_config
"""
from unittest.mock import patch

from werkzeug.datastructures import ImmutableMultiDict

from scoring_engine.models.account import Account
from scoring_engine.models.environment import Environment
from scoring_engine.models.property import Property
from scoring_engine.models.service import Service
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team
from scoring_engine.models.user import User
from scoring_engine.web import create_app
from scoring_engine.web.views.api.setup import (
    _build_config,
    _parse_bracket_form,
    _validate_config,
)
from tests.scoring_engine.unit_test import UnitTest


# ---------------------------------------------------------------------------
# Pure-function tests — no Flask context needed
# ---------------------------------------------------------------------------

class TestParseBracketForm:
    def _form(self, data):
        return ImmutableMultiDict(data)

    def test_top_level_scalars(self):
        result = _parse_bracket_form(self._form([
            ("competition_name", "Test Comp"),
            ("scoring_interval", "300"),
        ]))
        assert result["competition_name"] == "Test Comp"
        assert result["scoring_interval"] == "300"

    def test_teams_list(self):
        result = _parse_bracket_form(self._form([
            ("teams[0][name]", "Alpha"),
            ("teams[0][username]", "alpha_user"),
            ("teams[0][password]", "pass1"),
            ("teams[1][name]", "Bravo"),
            ("teams[1][username]", "bravo_user"),
            ("teams[1][password]", "pass2"),
        ]))
        assert isinstance(result["teams"], list)
        assert len(result["teams"]) == 2
        assert result["teams"][0]["name"] == "Alpha"
        assert result["teams"][1]["username"] == "bravo_user"

    def test_services_with_accounts(self):
        result = _parse_bracket_form(self._form([
            ("services[0][name]", "SSH"),
            ("services[0][check_name]", "SSHCheck"),
            ("services[0][port]", "22"),
            ("services[0][accounts][0][username]", "svcuser"),
            ("services[0][accounts][0][password]", "svcpass"),
        ]))
        svc = result["services"][0]
        assert svc["name"] == "SSH"
        assert svc["accounts"][0]["username"] == "svcuser"

    def test_team_hosts_string_keys_stay_as_dict(self):
        result = _parse_bracket_form(self._form([
            ("services[0][team_hosts][Alpha Team]", "192.168.1.10"),
            ("services[0][team_hosts][Bravo Team]", "192.168.1.20"),
        ]))
        hosts = result["services"][0]["team_hosts"]
        assert isinstance(hosts, dict)
        assert hosts["Alpha Team"] == "192.168.1.10"
        assert hosts["Bravo Team"] == "192.168.1.20"

    def test_service_properties(self):
        result = _parse_bracket_form(self._form([
            ("services[0][properties][0][name]", "commands"),
            ("services[0][properties][0][value]", "id"),
        ]))
        prop = result["services"][0]["properties"][0]
        assert prop["name"] == "commands"
        assert prop["value"] == "id"

    def test_empty_form(self):
        assert _parse_bracket_form(self._form([])) == {}

    def test_integer_keys_sorted_correctly(self):
        result = _parse_bracket_form(self._form([
            ("teams[2][name]", "Charlie"),
            ("teams[0][name]", "Alpha"),
            ("teams[1][name]", "Bravo"),
        ]))
        assert result["teams"][0]["name"] == "Alpha"
        assert result["teams"][1]["name"] == "Bravo"
        assert result["teams"][2]["name"] == "Charlie"


class TestBuildConfig:
    def _parsed(self, overrides=None):
        base = {
            "admin_username": "whiteteamuser",
            "admin_password": "adminpass",
            "red_team_username": "redteamuser",
            "red_team_password": "redpass",
            "competition_name": "Test Comp",
            "scoring_interval": "300",
            "teams": [{"name": "Alpha", "username": "alpha_user", "password": "pass1"}],
            "services": [{
                "name": "SSH",
                "check_name": "SSHCheck",
                "port": "22",
                "points": "100",
                "matching_content": "uid=",
                "team_hosts": {"Alpha": "192.168.1.10"},
                "accounts": [{"username": "svcuser", "password": "svcpass"}],
                "properties": [{"name": "commands", "value": "id"}],
            }],
        }
        if overrides:
            base.update(overrides)
        return base

    def test_admin_fields_mapped(self):
        config = _build_config(self._parsed())
        assert config["admin"]["admin_username"] == "whiteteamuser"
        assert config["admin"]["admin_password"] == "adminpass"

    def test_red_team_fields_mapped(self):
        config = _build_config(self._parsed())
        assert config["red_team"]["username"] == "redteamuser"
        assert config["red_team"]["password"] == "redpass"

    def test_competition_fields_mapped(self):
        config = _build_config(self._parsed())
        assert config["competition"]["competition_name"] == "Test Comp"
        assert config["competition"]["scoring_interval"] == "300"

    def test_teams_mapped(self):
        config = _build_config(self._parsed())
        assert len(config["teams"]) == 1
        assert config["teams"][0]["name"] == "Alpha"

    def test_service_port_coerced_to_int(self):
        config = _build_config(self._parsed())
        assert config["services"][0]["port"] == 22

    def test_service_points_coerced_to_int(self):
        config = _build_config(self._parsed())
        assert config["services"][0]["points"] == 100

    def test_service_worker_queue_defaults_to_main(self):
        config = _build_config(self._parsed())
        assert config["services"][0]["worker_queue"] == "main"

    def test_service_environments_built_from_matching_content_and_properties(self):
        config = _build_config(self._parsed())
        envs = config["services"][0]["environments"]
        assert len(envs) == 1
        assert envs[0]["matching_content"] == "uid="
        assert envs[0]["properties"] == [{"name": "commands", "value": "id"}]

    def test_service_accounts_included(self):
        config = _build_config(self._parsed())
        assert config["services"][0]["accounts"] == [{"username": "svcuser", "password": "svcpass"}]

    def test_team_hosts_preserved(self):
        config = _build_config(self._parsed())
        assert config["services"][0]["team_hosts"] == {"Alpha": "192.168.1.10"}

    def test_empty_accounts_list_when_none(self):
        parsed = self._parsed()
        parsed["services"][0]["accounts"] = []
        config = _build_config(parsed)
        assert config["services"][0]["accounts"] == []

    def test_missing_properties_gives_empty_env_properties(self):
        parsed = self._parsed()
        parsed["services"][0]["properties"] = []
        config = _build_config(parsed)
        assert config["services"][0]["environments"][0]["properties"] == []


class TestValidateConfig:
    def _valid_config(self):
        return {
            "competition": {"competition_name": "Test Comp", "scoring_interval": "300"},
            "admin": {"admin_username": "whiteteamuser", "admin_password": "adminpass"},
            "red_team": {"username": "redteamuser", "password": "redpass"},
            "teams": [{"name": "Alpha", "username": "alpha_user", "password": "pass1"}],
            "services": [{
                "name": "SSH",
                "check_name": "SSHCheck",
                "port": 22,
                "points": 100,
                "worker_queue": "main",
                "team_hosts": {"Alpha": "192.168.1.10"},
                "accounts": [{"username": "svcuser", "password": "svcpass"}],
                "environments": [{"matching_content": "uid=", "properties": [{"name": "commands", "value": "id"}]}],
            }],
        }

    def test_valid_config_returns_no_errors(self):
        assert _validate_config(self._valid_config()) == []

    def test_missing_competition_name(self):
        config = self._valid_config()
        config["competition"]["competition_name"] = ""
        errors = _validate_config(config)
        assert any("Competition name" in e for e in errors)

    def test_scoring_interval_too_low(self):
        config = self._valid_config()
        config["competition"]["scoring_interval"] = "0"
        errors = _validate_config(config)
        assert any("Scoring interval" in e for e in errors)

    def test_scoring_interval_too_high(self):
        config = self._valid_config()
        config["competition"]["scoring_interval"] = "9999"
        errors = _validate_config(config)
        assert any("Scoring interval" in e for e in errors)

    def test_scoring_interval_boundary_valid(self):
        config = self._valid_config()
        config["competition"]["scoring_interval"] = "60"
        assert _validate_config(config) == []

    def test_scoring_interval_non_numeric_defaults_to_300(self):
        config = self._valid_config()
        config["competition"]["scoring_interval"] = "abc"
        # _to_int falls back to 300, which is valid
        assert _validate_config(config) == []

    def test_missing_admin_username(self):
        config = self._valid_config()
        config["admin"]["admin_username"] = ""
        errors = _validate_config(config)
        assert any("Admin username" in e for e in errors)

    def test_missing_admin_password(self):
        config = self._valid_config()
        config["admin"]["admin_password"] = ""
        errors = _validate_config(config)
        assert any("Admin password" in e for e in errors)

    def test_missing_red_team_username(self):
        config = self._valid_config()
        config["red_team"]["username"] = ""
        errors = _validate_config(config)
        assert any("Red team username" in e for e in errors)

    def test_missing_red_team_password(self):
        config = self._valid_config()
        config["red_team"]["password"] = ""
        errors = _validate_config(config)
        assert any("Red team password" in e for e in errors)

    def test_no_teams(self):
        config = self._valid_config()
        config["teams"] = []
        errors = _validate_config(config)
        assert any("blue team" in e for e in errors)

    def test_team_missing_username(self):
        config = self._valid_config()
        config["teams"][0]["username"] = ""
        errors = _validate_config(config)
        assert any("missing a username" in e for e in errors)

    def test_team_missing_password(self):
        config = self._valid_config()
        config["teams"][0]["password"] = ""
        errors = _validate_config(config)
        assert any("missing a password" in e for e in errors)

    def test_duplicate_username_admin_and_team(self):
        config = self._valid_config()
        config["teams"][0]["username"] = "whiteteamuser"
        errors = _validate_config(config)
        assert any("Duplicate username" in e for e in errors)

    def test_duplicate_username_red_and_team(self):
        config = self._valid_config()
        config["teams"][0]["username"] = "redteamuser"
        errors = _validate_config(config)
        assert any("Duplicate username" in e for e in errors)

    def test_no_services(self):
        config = self._valid_config()
        config["services"] = []
        errors = _validate_config(config)
        assert any("service" in e for e in errors)

    def test_unknown_check_name(self):
        config = self._valid_config()
        config["services"][0]["check_name"] = "FakeCheck"
        errors = _validate_config(config)
        assert any("Unknown check type" in e for e in errors)

    def test_invalid_port(self):
        config = self._valid_config()
        config["services"][0]["port"] = 99999
        errors = _validate_config(config)
        assert any("invalid port" in e for e in errors)

    def test_zero_points(self):
        config = self._valid_config()
        config["services"][0]["points"] = 0
        errors = _validate_config(config)
        assert any("points" in e for e in errors)

    def test_missing_team_host(self):
        config = self._valid_config()
        config["services"][0]["team_hosts"] = {}
        errors = _validate_config(config)
        assert any("missing hosts" in e for e in errors)


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

class TestSetupEndpoint(UnitTest):
    def setup_method(self):
        super().setup_method()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()

    def teardown_method(self):
        self.ctx.pop()
        super().teardown_method()

    def _valid_form(self):
        return {
            "competition_name": "Test Comp",
            "scoring_interval": "300",
            "admin_username": "whiteteamuser",
            "admin_password": "adminpass",
            "red_team_username": "redteamuser",
            "red_team_password": "redpass",
            "teams[0][name]": "Alpha",
            "teams[0][username]": "alpha_user",
            "teams[0][password]": "pass1",
            "services[0][name]": "SSH",
            "services[0][check_name]": "SSHCheck",
            "services[0][port]": "22",
            "services[0][points]": "100",
            "services[0][matching_content]": "uid=",
            "services[0][team_hosts][Alpha]": "192.168.1.10",
            "services[0][accounts][0][username]": "svcuser",
            "services[0][accounts][0][password]": "svcpass",
            "services[0][properties][0][name]": "commands",
            "services[0][properties][0][value]": "id",
        }

    # GET /setup tests

    def test_get_setup_returns_200_when_unconfigured(self):
        resp = self.client.get("/setup")
        assert resp.status_code == 200

    def test_get_setup_redirects_to_login_when_configured(self):
        blue_team = Team(name="BlueTeam", color="Blue")
        self.session.add(blue_team)
        self.session.add(User(username="blueuser", password="pass", team=blue_team))
        self.session.commit()
        resp = self.client.get("/setup")
        assert resp.status_code == 302
        assert "/login" in resp.location

    # POST /api/setup tests

    def test_valid_submission_returns_200_ok(self):
        resp = self.client.post("/api/setup", data=self._valid_form())
        assert resp.status_code == 200
        assert resp.json["status"] == "ok"

    def test_valid_submission_creates_white_team_and_admin_user(self):
        self.client.post("/api/setup", data=self._valid_form())
        white_teams = self.session.query(Team).filter_by(color="White").all()
        assert len(white_teams) == 1
        admin = self.session.query(User).filter_by(username="whiteteamuser").first()
        assert admin is not None
        assert admin.team.color == "White"

    def test_valid_submission_creates_red_team_and_user(self):
        self.client.post("/api/setup", data=self._valid_form())
        red_teams = self.session.query(Team).filter_by(color="Red").all()
        assert len(red_teams) == 1
        reduser = self.session.query(User).filter_by(username="redteamuser").first()
        assert reduser is not None
        assert reduser.team.color == "Red"

    def test_valid_submission_creates_blue_team_and_user(self):
        self.client.post("/api/setup", data=self._valid_form())
        blue_teams = self.session.query(Team).filter_by(color="Blue").all()
        assert len(blue_teams) == 1
        assert blue_teams[0].name == "Alpha"
        alpha_user = self.session.query(User).filter_by(username="alpha_user").first()
        assert alpha_user is not None

    def test_already_configured_returns_403(self):
        blue_team = Team(name="BlueTeam", color="Blue")
        self.session.add(blue_team)
        self.session.add(User(username="blueuser", password="pass", team=blue_team))
        self.session.commit()
        resp = self.client.post("/api/setup", data=self._valid_form())
        assert resp.status_code == 403
        assert resp.json["status"] == "error"

    def test_missing_competition_name_returns_400(self):
        form = self._valid_form()
        form["competition_name"] = ""
        resp = self.client.post("/api/setup", data=form)
        assert resp.status_code == 400
        assert resp.json["status"] == "error"

    def test_unknown_check_type_returns_400(self):
        form = self._valid_form()
        form["services[0][check_name]"] = "BogusCheck"
        resp = self.client.post("/api/setup", data=form)
        assert resp.status_code == 400
        assert "Unknown check type" in resp.json["message"]

    def test_duplicate_username_returns_400(self):
        form = self._valid_form()
        form["teams[0][username]"] = "whiteteamuser"  # same as admin_username
        resp = self.client.post("/api/setup", data=form)
        assert resp.status_code == 400
        assert "Duplicate" in resp.json["message"]

    def test_no_blue_teams_created_on_validation_failure(self):
        form = self._valid_form()
        form["competition_name"] = ""
        self.client.post("/api/setup", data=form)
        blue_teams = self.session.query(Team).filter_by(color="Blue").all()
        assert len(blue_teams) == 0

    # Service creation

    def test_service_created_for_blue_team(self):
        self.client.post("/api/setup", data=self._valid_form())
        services = self.session.query(Service).all()
        assert len(services) == 1
        assert services[0].name == "SSH"
        assert services[0].check_name == "SSHCheck"
        assert services[0].port == 22
        assert services[0].points == 100
        assert services[0].host == "192.168.1.10"

    def test_service_belongs_to_blue_team(self):
        self.client.post("/api/setup", data=self._valid_form())
        service = self.session.query(Service).first()
        assert service.team.color == "Blue"
        assert service.team.name == "Alpha"

    def test_service_worker_queue_defaults_to_main(self):
        self.client.post("/api/setup", data=self._valid_form())
        service = self.session.query(Service).first()
        assert service.worker_queue == "main"

    # Account creation

    def test_account_created_for_service(self):
        self.client.post("/api/setup", data=self._valid_form())
        accounts = self.session.query(Account).all()
        assert len(accounts) == 1
        assert accounts[0].username == "svcuser"

    def test_account_linked_to_service(self):
        self.client.post("/api/setup", data=self._valid_form())
        service = self.session.query(Service).first()
        assert len(service.accounts) == 1
        assert service.accounts[0].username == "svcuser"

    # Environment and property creation

    def test_environment_created_for_service(self):
        self.client.post("/api/setup", data=self._valid_form())
        environments = self.session.query(Environment).all()
        assert len(environments) == 1
        assert environments[0].matching_content == "uid="

    def test_environment_linked_to_service(self):
        self.client.post("/api/setup", data=self._valid_form())
        service = self.session.query(Service).first()
        assert len(service.environments) == 1

    def test_property_created_for_environment(self):
        self.client.post("/api/setup", data=self._valid_form())
        properties = self.session.query(Property).all()
        assert len(properties) == 1
        assert properties[0].name == "commands"
        assert properties[0].value == "id"

    def test_property_linked_to_environment(self):
        self.client.post("/api/setup", data=self._valid_form())
        env = self.session.query(Environment).first()
        assert len(env.properties) == 1

    # Multiple blue teams — each gets its own service row with its own host

    def _two_team_form(self):
        form = self._valid_form()
        form.update({
            "teams[1][name]": "Bravo",
            "teams[1][username]": "bravo_user",
            "teams[1][password]": "pass2",
            "services[0][team_hosts][Bravo]": "192.168.2.10",
        })
        return form

    def test_two_teams_each_get_a_service_row(self):
        self.client.post("/api/setup", data=self._two_team_form())
        services = self.session.query(Service).all()
        assert len(services) == 2

    def test_two_teams_service_hosts_are_correct(self):
        self.client.post("/api/setup", data=self._two_team_form())
        hosts = {s.team.name: s.host for s in self.session.query(Service).all()}
        assert hosts["Alpha"] == "192.168.1.10"
        assert hosts["Bravo"] == "192.168.2.10"

    def test_two_teams_each_get_their_own_environment(self):
        self.client.post("/api/setup", data=self._two_team_form())
        assert self.session.query(Environment).count() == 2

    def test_two_teams_each_get_their_own_account(self):
        self.client.post("/api/setup", data=self._two_team_form())
        assert self.session.query(Account).count() == 2

    def test_two_teams_each_get_their_own_property(self):
        self.client.post("/api/setup", data=self._two_team_form())
        assert self.session.query(Property).count() == 2

    def test_two_services_two_teams_creates_four_service_rows(self):
        form = self._two_team_form()
        form.update({
            "services[1][name]": "HTTP",
            "services[1][check_name]": "HTTPCheck",
            "services[1][port]": "80",
            "services[1][points]": "100",
            "services[1][matching_content]": "200 OK",
            "services[1][team_hosts][Alpha]": "192.168.1.10",
            "services[1][team_hosts][Bravo]": "192.168.2.10",
            "services[1][properties][0][name]": "uri",
            "services[1][properties][0][value]": "/",
        })
        self.client.post("/api/setup", data=form)
        assert self.session.query(Service).count() == 4

    # Settings seeding

    def test_settings_seeded_after_setup(self):
        self.client.post("/api/setup", data=self._valid_form())
        required_settings = [
            "target_round_time",
            "worker_refresh_time",
            "engine_paused",
            "pause_duration",
            "blue_team_update_hostname",
            "blue_team_update_port",
            "blue_team_update_account_usernames",
            "blue_team_update_account_passwords",
            "blue_team_view_check_output",
            "blue_team_view_status_page",
            "blue_team_view_current_status",
            "blue_team_view_historical_status",
            "about_page_content",
            "welcome_page_content",
            "agent_checkin_interval_sec",
            "agent_show_flag_early_mins",
            "agent_psk",
        ]
        seeded = {
            s.name for s in self.session.query(Setting).all()
        }
        for name in required_settings:
            assert name in seeded, f"Missing setting: {name}"

    def test_target_round_time_set_from_scoring_interval(self):
        form = self._valid_form()
        form["scoring_interval"] = "180"
        self.client.post("/api/setup", data=form)
        setting = self.session.query(Setting).filter_by(name="target_round_time").order_by(Setting.id.desc()).first()
        assert int(setting.value) == 180

    def test_welcome_page_content_seeded(self):
        self.client.post("/api/setup", data=self._valid_form())
        setting = self.session.query(Setting).filter_by(name="welcome_page_content").order_by(Setting.id.desc()).first()
        assert setting is not None
        assert setting.value != ""

    # GET /api/setup/checks

    def test_checks_endpoint_returns_200(self):
        resp = self.client.get("/api/setup/checks")
        assert resp.status_code == 200

    def test_checks_endpoint_returns_dict(self):
        resp = self.client.get("/api/setup/checks")
        assert "checks" in resp.json
        assert isinstance(resp.json["checks"], dict)
        assert len(resp.json["checks"]) > 0

    def test_checks_endpoint_includes_ssh(self):
        resp = self.client.get("/api/setup/checks")
        assert "SSHCheck" in resp.json["checks"]

    def test_checks_endpoint_ssh_has_metadata(self):
        resp = self.client.get("/api/setup/checks")
        ssh = resp.json["checks"]["SSHCheck"]
        assert ssh["uses_accounts"] is True
        assert "commands" in ssh["required_properties"]

    def test_checks_endpoint_icmp_no_accounts(self):
        resp = self.client.get("/api/setup/checks")
        icmp = resp.json["checks"]["ICMPCheck"]
        assert icmp["uses_accounts"] is False
        assert icmp["required_properties"] == []
