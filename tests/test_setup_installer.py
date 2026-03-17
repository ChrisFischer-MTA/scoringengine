"""
Tests for setup_installer.py — covers all functions that don't require Docker.
"""
import pytest
import yaml
import setup_installer
from setup_installer import (
    _set_ini_value,
    redact_config_for_print,
    KNOWN_CHECKS,
)
from scoring_engine.competition import Competition


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Redirect all file output paths to a temp directory so tests never touch the real repo."""
    competition_yaml = tmp_path / "bin" / "competition.yaml"
    compose_override = tmp_path / "docker-compose.override.yml"
    monkeypatch.setattr(setup_installer, "COMPETITION_YAML", competition_yaml)
    monkeypatch.setattr(setup_installer, "COMPOSE_OVERRIDE", compose_override)
    return tmp_path


@pytest.fixture
def competition_yaml(isolated_paths):
    return isolated_paths / "bin" / "competition.yaml"


@pytest.fixture
def compose_override(isolated_paths):
    return isolated_paths / "docker-compose.override.yml"


@pytest.fixture
def base_config():
    """Minimal wizard config with 2 blue teams and 2 services."""
    return {
        "admin": {"admin_username": "admin", "admin_password": "adminpass"},
        "red_team": {"username": "redteam", "password": "redpass"},
        "competition": {"competition_name": "Test Comp", "scoring_interval": "180"},
        "teams": [
            {"name": "Red Dragons", "username": "dragons_user", "password": "pass1"},
            {"name": "Blue Phoenix", "username": "phoenix_user", "password": "pass2"},
        ],
        "services": [
            {
                "name": "SSH",
                "check_name": "SSHCheck",
                "port": 22,
                "points": 100,
                "worker_queue": "main",
                "team_hosts": {
                    "Red Dragons": "192.168.1.11",
                    "Blue Phoenix": "192.168.1.21",
                },
                "accounts": [{"username": "svcuser", "password": "svcpass"}],
                "environments": [
                    {"matching_content": "uid=", "properties": [{"name": "commands", "value": "id"}]},
                ],
            },
            {
                "name": "HTTP",
                "check_name": "HTTPCheck",
                "port": 80,
                "points": 100,
                "worker_queue": "main",
                "team_hosts": {
                    "Red Dragons": "192.168.1.11",
                    "Blue Phoenix": "192.168.1.21",
                },
                "accounts": [],
                "environments": [
                    {
                        "matching_content": "Welcome",
                        "properties": [
                            {"name": "uri", "value": "/"},
                            {"name": "useragent", "value": "Mozilla/5.0"},
                            {"name": "vhost", "value": "__TEAM_HOST__"},
                        ],
                    },
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# write_competition_yaml
# ---------------------------------------------------------------------------

class TestWriteCompetitionYaml:
    def test_creates_file(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        assert competition_yaml.exists()

    def test_team_structure(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        colors = [t["color"] for t in data["teams"]]
        assert colors == ["White", "Red", "Blue", "Blue"]

    def test_white_team_uses_admin_credentials(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        white = data["teams"][0]
        assert white["users"][0]["username"] == "admin"
        assert white["users"][0]["password"] == "adminpass"

    def test_blue_team_names(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        blue_names = [t["name"] for t in data["teams"] if t["color"] == "Blue"]
        assert blue_names == ["Red Dragons", "Blue Phoenix"]

    def test_per_team_host_assignment(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        dragons = next(t for t in data["teams"] if t["name"] == "Red Dragons")
        phoenix = next(t for t in data["teams"] if t["name"] == "Blue Phoenix")
        dragons_ssh = next(s for s in dragons["services"] if s["name"] == "SSH")
        phoenix_ssh = next(s for s in phoenix["services"] if s["name"] == "SSH")
        assert dragons_ssh["host"] == "192.168.1.11"
        assert phoenix_ssh["host"] == "192.168.1.21"

    def test_team_host_placeholder_resolved_in_vhost(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        dragons = next(t for t in data["teams"] if t["name"] == "Red Dragons")
        phoenix = next(t for t in data["teams"] if t["name"] == "Blue Phoenix")
        dragons_http = next(s for s in dragons["services"] if s["name"] == "HTTP")
        phoenix_http = next(s for s in phoenix["services"] if s["name"] == "HTTP")
        dragons_vhost = next(p for p in dragons_http["environments"][0]["properties"] if p["name"] == "vhost")
        phoenix_vhost = next(p for p in phoenix_http["environments"][0]["properties"] if p["name"] == "vhost")
        assert dragons_vhost["value"] == "192.168.1.11"
        assert phoenix_vhost["value"] == "192.168.1.21"

    def test_multiple_environments_written(self, base_config, competition_yaml):
        """Each environment in the config produces a separate environment entry in the YAML."""
        base_config["services"][0]["environments"].append(
            {"matching_content": "PID", "properties": [{"name": "commands", "value": "ps"}]}
        )
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        dragons = next(t for t in data["teams"] if t["name"] == "Red Dragons")
        ssh = next(s for s in dragons["services"] if s["name"] == "SSH")
        assert len(ssh["environments"]) == 2
        assert ssh["environments"][0]["matching_content"] == "uid="
        assert ssh["environments"][1]["matching_content"] == "PID"
        assert ssh["environments"][1]["properties"][0] == {"name": "commands", "value": "ps"}

    def test_worker_queue_omitted_when_default(self, base_config, competition_yaml):
        """worker_queue='main' (the default) is not written to YAML."""
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        dragons = next(t for t in data["teams"] if t["name"] == "Red Dragons")
        ssh = next(s for s in dragons["services"] if s["name"] == "SSH")
        assert "worker_queue" not in ssh

    def test_worker_queue_written_when_non_default(self, base_config, competition_yaml):
        """Custom worker_queue values are written to YAML."""
        base_config["services"][0]["worker_queue"] = "team_a_worker"
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        dragons = next(t for t in data["teams"] if t["name"] == "Red Dragons")
        ssh = next(s for s in dragons["services"] if s["name"] == "SSH")
        assert ssh["worker_queue"] == "team_a_worker"

    def test_flags_key_present_and_empty(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        assert "flags" in data
        assert data["flags"] == []

    def test_service_accounts_included(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        dragons = next(t for t in data["teams"] if t["name"] == "Red Dragons")
        ssh = next(s for s in dragons["services"] if s["name"] == "SSH")
        assert ssh["accounts"] == [{"username": "svcuser", "password": "svcpass"}]

    def test_service_without_accounts_omits_accounts_key(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        data = yaml.safe_load(competition_yaml.read_text())
        dragons = next(t for t in data["teams"] if t["name"] == "Red Dragons")
        http = next(s for s in dragons["services"] if s["name"] == "HTTP")
        assert "accounts" not in http

    def test_output_passes_competition_parser(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        yaml_str = competition_yaml.read_text()
        comp = Competition.parse_yaml_str(yaml_str)
        blue_teams = [t for t in comp["teams"] if t["color"] == "Blue"]
        assert len(blue_teams) == 2


# ---------------------------------------------------------------------------
# check_already_configured
# ---------------------------------------------------------------------------

class TestCheckAlreadyConfigured:
    def test_returns_false_when_file_missing(self, competition_yaml):
        assert competition_yaml.exists() is False
        assert setup_installer.check_already_configured() is False

    def test_returns_false_when_no_blue_teams(self, competition_yaml):
        competition_yaml.parent.mkdir(parents=True, exist_ok=True)
        competition_yaml.write_text("teams:\n- name: WhiteTeam\n  color: White\n  users: []\n")
        assert setup_installer.check_already_configured() is False

    def test_returns_true_when_blue_team_exists(self, base_config, competition_yaml):
        setup_installer.write_competition_yaml(base_config)
        assert setup_installer.check_already_configured() is True

    def test_returns_false_on_malformed_yaml(self, competition_yaml):
        competition_yaml.parent.mkdir(parents=True, exist_ok=True)
        competition_yaml.write_text("this: is: not: valid: yaml: {{{")
        assert setup_installer.check_already_configured() is False


# ---------------------------------------------------------------------------
# write_compose_override
# ---------------------------------------------------------------------------

class TestWriteComposeOverride:
    def test_creates_file(self, compose_override):
        setup_installer.write_compose_override()
        assert compose_override.exists()

    def test_contains_all_four_services(self, compose_override):
        setup_installer.write_compose_override()
        content = compose_override.read_text()
        for service in ("bootstrap", "engine", "worker", "web"):
            assert service in content

    def test_mounts_correct_path(self, compose_override):
        setup_installer.write_compose_override()
        content = compose_override.read_text()
        assert "./docker/engine.conf.inc:/app/engine.conf:ro" in content


# ---------------------------------------------------------------------------
# _set_ini_value
# ---------------------------------------------------------------------------

class TestSetIniValue:
    def test_replaces_active_key(self):
        text = "[OPTIONS]\ndb_uri = old_value\n"
        result = _set_ini_value(text, "db_uri", "new_value")
        assert "db_uri = new_value" in result
        assert "old_value" not in result

    def test_uncomments_commented_key(self):
        text = "[OPTIONS]\n#db_uri = example\n"
        result = _set_ini_value(text, "db_uri", "new_value")
        assert "db_uri = new_value" in result

    def test_appends_under_options_if_key_missing(self):
        text = "[OPTIONS]\nsome_key = val\n"
        result = _set_ini_value(text, "new_key", "new_value")
        assert "new_key = new_value" in result


# ---------------------------------------------------------------------------
# redact_config_for_print
# ---------------------------------------------------------------------------

class TestRedactConfigForPrint:
    def test_redacts_db_password(self):
        config = {"database": {"password": "secret", "uri": "mysql://..."}}
        result = redact_config_for_print(config)
        assert result["database"]["password"] == "********"
        assert result["database"]["uri"] == "<redacted>"

    def test_redacts_admin_password(self):
        config = {"admin": {"admin_username": "admin", "admin_password": "secret"}}
        result = redact_config_for_print(config)
        assert result["admin"]["admin_password"] == "********"

    def test_redacts_team_passwords(self):
        config = {"teams": [{"name": "t1", "password": "secret"}]}
        result = redact_config_for_print(config)
        assert result["teams"][0]["password"] == "********"

    def test_redacts_service_account_passwords(self):
        config = {"services": [{"accounts": [{"username": "u", "password": "secret"}]}]}
        result = redact_config_for_print(config)
        assert result["services"][0]["accounts"][0]["password"] == "********"

    def test_does_not_mutate_original(self):
        config = {"admin": {"admin_password": "secret"}}
        redact_config_for_print(config)
        assert config["admin"]["admin_password"] == "secret"


# ---------------------------------------------------------------------------
# KNOWN_CHECKS matches actual check required_properties
# ---------------------------------------------------------------------------

class TestKnownChecksMatchActualChecks:
    """Verify KNOWN_CHECKS stays in sync with the actual check source files."""

    def _load_check_class(self, check_name):
        import importlib
        module_name = check_name.replace("Check", "").lower()
        mod = importlib.import_module(f"scoring_engine.checks.{module_name}")
        return getattr(mod, check_name)

    @pytest.mark.parametrize("check_name", KNOWN_CHECKS.keys())
    def test_required_properties_match(self, check_name):
        cls = self._load_check_class(check_name)
        expected = set(KNOWN_CHECKS[check_name]["required_properties"])
        actual = set(cls.required_properties)
        assert expected == actual, (
            f"{check_name}: KNOWN_CHECKS has {expected}, actual class has {actual}"
        )
