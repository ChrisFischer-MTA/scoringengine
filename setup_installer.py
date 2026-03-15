#!/usr/bin/env python3
import os
import re
import sys
import json
import time
import yaml
import getpass
import shutil
import subprocess
import argparse
from copy import deepcopy
from pathlib import Path

CONFIG_DIR = Path.cwd()
ENV_FILE = CONFIG_DIR / ".env"
COMPETITION_YAML = CONFIG_DIR / "bin" / "competition.yaml"
COMPOSE_OVERRIDE = CONFIG_DIR / "docker-compose.override.yml"

# compose file bind-mounts this path:
DOCKER_ENGINE_CONF = CONFIG_DIR / "docker" / "engine.conf.inc"
ENGINE_CONF_TEMPLATE = CONFIG_DIR / "engine.conf.inc"

# Check types: required_properties must match what each Check class declares,
# needs_accounts=True means the checker logs in with a username/password.
KNOWN_CHECKS = {
    "SSHCheck":   {"default_port": "22",   "required_properties": ["commands"], "needs_accounts": True},
    "HTTPCheck":  {"default_port": "80",   "required_properties": ["useragent", "vhost", "uri"],
                   "needs_accounts": False},
    "HTTPSCheck": {"default_port": "443",  "required_properties": ["useragent", "vhost", "uri"],
                   "needs_accounts": False},
    "RDPCheck":   {"default_port": "3389", "required_properties": [], "needs_accounts": True},
    "WinRMCheck": {"default_port": "5985", "required_properties": ["commands"], "needs_accounts": True},
    "FTPCheck":   {"default_port": "21",   "required_properties": ["remotefilepath", "filecontents"],
                   "needs_accounts": True},
    "ICMPCheck":  {"default_port": "0",    "required_properties": [], "needs_accounts": False},
    "DNSCheck":   {"default_port": "53",   "required_properties": ["qtype", "domain"], "needs_accounts": False},
}

TOTAL_STEPS = 7


# UI / prompt helpers
def clear():
    os.system("cls" if os.name == "nt" else "clear")


def prompt(msg, default=None, required=False, is_password=False, allow_blank=False):
    while True:
        if is_password:
            val = getpass.getpass(f"{msg}: ").strip()
        else:
            val = input(f"{msg} [{default}]: ").strip() if default is not None else input(f"{msg}: ").strip()

        if not val:
            if default is not None and default != "":
                return default
            if allow_blank:
                return ""
            if required:
                print("  This field is required.")
                continue
            return ""
        return val


def redact_config_for_print(config: dict) -> dict:
    c = deepcopy(config)
    if "engine" in c and "agent_psk" in c["engine"]:
        c["engine"]["agent_psk"] = "********"
    if "database" in c and "password" in c["database"]:
        c["database"]["password"] = "********"
    if "database" in c and "uri" in c["database"]:
        c["database"]["uri"] = "<redacted>"
    if "admin" in c and "admin_password" in c["admin"]:
        c["admin"]["admin_password"] = "********"
    if "redis" in c and c["redis"].get("redis_password"):
        c["redis"]["redis_password"] = "********"
    for team in c.get("teams", []):
        team["password"] = "********"
    for svc in c.get("services", []):
        for acct in svc.get("accounts", []):
            acct["password"] = "********"
    return c


# docker helpers
def require_docker():
    if shutil.which("docker") is None:
        sys.exit("Docker is required but was not found in PATH. Install Docker Desktop / docker engine first.")

    code, out = run_cmd(["docker", "compose", "version"], check=False, capture=True)
    if code != 0:
        print(out)
        sys.exit("Docker Compose is required but not working. Make sure Docker is running and compose is available.")


def run_cmd(cmd, check=True, capture=False):
    """Runs a command and returns (returncode, output_str)."""
    try:
        if capture:
            res = subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            return res.returncode, res.stdout
        else:
            res = subprocess.run(cmd, check=check)
            return res.returncode, ""
    except subprocess.CalledProcessError as e:
        out = ""
        if hasattr(e, "stdout") and e.stdout:
            out = e.stdout
        return e.returncode, out


def docker_compose_up(services):
    print(f"→ Starting services: {', '.join(services)}")
    code, out = run_cmd(["docker", "compose", "up", "-d", *services], check=False, capture=True)
    if code != 0:
        print(out)
        sys.exit("Failed to start required docker services.")


def docker_compose_down_volumes():
    run_cmd(["docker", "compose", "down", "-v"], check=False, capture=True)


def wait_for_tcp_inside_network(host, port, timeout_s=90):
    """
    Waits for host:port to become reachable from inside the compose network,
    by running a small python connect check inside the bootstrap container.
    """
    start = time.time()
    last_out = ""
    port = int(port)

    while time.time() - start < timeout_s:
        py = (
            "import socket,sys;"
            f"h={host!r};p={port};"
            "s=socket.socket();s.settimeout(2);"
            "rc=s.connect_ex((h,p));"
            "print(rc);"
            "sys.exit(0 if rc==0 else 1)"
        )
        cmd = ["docker", "compose", "run", "--rm", "bootstrap", "python3", "-c", py]
        code, out = run_cmd(cmd, check=False, capture=True)
        if code == 0:
            return True, ""
        last_out = out.strip()
        time.sleep(2)

    return False, last_out or f"Timed out waiting for {host}:{port}"


def test_db_uri_inside_docker(db_uri, timeout_s=60):
    """Validates DB credentials by connecting and running SELECT 1 inside bootstrap container."""
    start = time.time()
    last = ""
    while time.time() - start < timeout_s:
        py = (
            "import sys;"
            "from sqlalchemy import create_engine, text;"
            f"uri={db_uri!r};"
            "eng=create_engine(uri, pool_pre_ping=True);"
            "conn=eng.connect();"
            "conn.execute(text('SELECT 1'));"
            "conn.close();"
            "print('db ok');"
        )
        cmd = ["docker", "compose", "run", "--rm", "bootstrap", "python3", "-c", py]
        code, out = run_cmd(cmd, check=False, capture=True)
        if code == 0:
            return True, ""
        last = out.strip()
        time.sleep(2)
    return False, last


def test_redis_inside_docker(host, port, password=""):
    py = (
        "import redis;"
        f"host={host!r};port=int({port!r});pw={password!r};"
        "r=redis.Redis(host=host, port=port, password=(pw or None), socket_connect_timeout=2);"
        "r.ping();"
        "print('redis ok');"
    )
    cmd = ["docker", "compose", "run", "--rm", "bootstrap", "python3", "-c", py]
    code, out = run_cmd(cmd, check=False, capture=True)
    return code == 0, out.strip()


def run_bootstrap_once():
    """Runs the bootstrap service and waits for it to complete successfully."""
    print("→ Running bootstrap (schema init / seed)...")
    code, out = run_cmd(["docker", "compose", "up", "--no-deps", "--abort-on-container-exit", "bootstrap"],
                        check=False, capture=True)
    if code != 0:
        print(out)
        return False, out.strip()
    return True, ""


# already-configured check
def check_already_configured():
    """Returns True if competition.yaml exists and already has at least one Blue team."""
    if not COMPETITION_YAML.exists():
        return False
    try:
        with open(COMPETITION_YAML) as f:
            data = yaml.safe_load(f)
        if not data or "teams" not in data:
            return False
        return any(t.get("color") == "Blue" for t in data["teams"])
    except Exception:
        return False


# config collection
def get_engine_settings(advanced=False):
    settings = {}
    if advanced:
        override = prompt("Override Agent PSK?", "n", required=True).strip().lower()
        if override in ("y", "yes"):
            settings["agent_psk"] = prompt("Agent PSK", "", required=True, is_password=True)
    return settings


def get_db_config():
    print(f"\n[1/{TOTAL_STEPS}] Database Configuration")
    host = prompt("  Database host", "mysql", required=True)
    port = prompt("  Database port", "3306", required=True)
    name = prompt("  Database name", "scoring_engine", required=True)
    user = prompt("  Database user", "se_user", required=True)
    pw = prompt("  Database password", "CHANGEME", required=True, is_password=True)
    uri = f"mysql://{user}:{pw}@{host}:{port}/{name}?charset=utf8mb4"
    return {"type": "mysql", "host": host, "port": port, "name": name, "user": user, "password": pw, "uri": uri}


def get_redis_config():
    print(f"\n[2/{TOTAL_STEPS}] Redis Configuration")
    redis_host = prompt("  Redis host", "redis", required=True)
    redis_port = prompt("  Redis port", "6379", required=True)
    redis_pw = prompt("  Redis password (leave blank if none)", "", allow_blank=True)
    return {"cache_type": "redis", "redis_host": redis_host, "redis_port": redis_port, "redis_password": redis_pw}


def get_competition_info():
    print(f"\n[3/{TOTAL_STEPS}] Competition Info")
    name = prompt("  Competition name", required=True)
    interval = prompt("  Scoring interval (seconds)", "300", required=True)
    return {"competition_name": name, "scoring_interval": interval}


def get_game_duration_config():
    print(f"\n[4/{TOTAL_STEPS}] Game Duration")
    print("  Leave start time blank to start immediately when the engine launches.")
    start_time = prompt("  Start time (YYYY-MM-DD HH:MM, or blank for immediate)", "", allow_blank=True)
    duration = prompt("  Duration in hours", "8", required=True)
    return {"start_time": start_time, "duration_hours": duration}


def get_admin_info():
    print(f"\n[5/{TOTAL_STEPS}] Admin Account")
    print("  This account will be the White Team organizer login.")
    username = prompt("  Username", "admin", required=True)
    pw = getpass.getpass("  Password: ")
    pw2 = getpass.getpass("  Confirm password: ")
    if pw != pw2:
        print("  Passwords do not match.")
        return get_admin_info()
    return {"admin_username": username, "admin_password": pw}


def get_teams_config():
    print(f"\n[6/{TOTAL_STEPS}] Teams")
    while True:
        try:
            count = int(prompt("  Number of Blue teams", "3", required=True))
            if count < 1:
                print("  Must have at least 1 team.")
                continue
            break
        except ValueError:
            print("  Enter a number.")

    teams = []
    for i in range(1, count + 1):
        print(f"\n  Team {i}:")
        name = prompt("    Name", f"team{i}", required=True)
        username = prompt("    Login username", f"team{i}user", required=True)
        pw = getpass.getpass("    Login password: ")
        teams.append({"name": name, "username": username, "password": pw})
    return teams


def _prompt_service_accounts():
    """Prompt for one or more username/password pairs the checker uses to log in."""
    accounts = []
    print("    Service accounts (credentials the checker logs in with):")
    while True:
        username = prompt("      Username (blank to finish)", "", allow_blank=True)
        if not username:
            if not accounts:
                print("      At least one account is required for this check type.")
                continue
            break
        password = getpass.getpass(f"      Password for {username}: ")
        accounts.append({"username": username, "password": password})
    return accounts


def _prompt_service_properties(check_name):
    """Prompt for the required properties of a given check type."""
    meta = KNOWN_CHECKS[check_name]
    properties = []

    if not meta["required_properties"]:
        return properties

    print("    Check properties:")
    if check_name in ("SSHCheck", "WinRMCheck"):
        commands = prompt("      Commands to run on the remote host", "id", required=True)
        properties.append({"name": "commands", "value": commands})

    elif check_name in ("HTTPCheck", "HTTPSCheck"):
        uri = prompt("      URI path", "/", required=True)
        properties.append({"name": "uri", "value": uri})
        properties.append({"name": "useragent", "value": "Mozilla/5.0"})
        # vhost is set to the team's host IP at YAML-write time
        properties.append({"name": "vhost", "value": "__TEAM_HOST__"})

    elif check_name == "FTPCheck":
        remotefilepath = prompt("      Remote file path (e.g. /pub/test.txt)", required=True)
        filecontents = prompt("      Expected file contents (text to match)", required=True)
        properties.append({"name": "remotefilepath", "value": remotefilepath})
        properties.append({"name": "filecontents", "value": filecontents})

    elif check_name == "DNSCheck":
        qtype = prompt("      Record type (e.g. A, MX, CNAME)", "A", required=True)
        domain = prompt("      Domain to query (e.g. example.com)", required=True)
        properties.append({"name": "qtype", "value": qtype})
        properties.append({"name": "domain", "value": domain})

    return properties


def get_services_config(teams):
    print(f"\n[7/{TOTAL_STEPS}] Services")
    print(f"  Available check types: {', '.join(KNOWN_CHECKS.keys())}")
    print("  All teams share the same services — enter a different host IP per team.\n")

    services = []
    while True:
        default_add = "y" if not services else "n"
        add = prompt("  Add a service?", default_add).lower()
        if not add.startswith("y"):
            if not services:
                print("  At least one service is required.")
                continue
            break

        print()
        svc = {}
        svc["name"] = prompt("    Service name (e.g. SSH, HTTP)", required=True)

        check_name = prompt("    Check type", required=True)
        while check_name not in KNOWN_CHECKS:
            print(f"    Unknown. Available: {', '.join(KNOWN_CHECKS.keys())}")
            check_name = prompt("    Check type", required=True)
        svc["check_name"] = check_name

        meta = KNOWN_CHECKS[check_name]
        svc["port"] = int(prompt("    Port", meta["default_port"], required=True))
        svc["points"] = int(prompt("    Points", "100", required=True))
        svc["matching_content"] = prompt("    Text that means the check passed", required=True)

        print("    Host IP for each team:")
        svc["team_hosts"] = {}
        for team in teams:
            host = prompt(f"      {team['name']}", required=True)
            svc["team_hosts"][team["name"]] = host

        svc["accounts"] = _prompt_service_accounts() if meta["needs_accounts"] else []
        svc["properties"] = _prompt_service_properties(check_name)

        services.append(svc)
        print(f"  Added: {svc['name']} ({check_name})")

    return services


def confirm_summary(config):
    print("\nSetup Summary (secrets redacted):")
    print(json.dumps(redact_config_for_print(config), indent=4))
    confirm = input("Confirm and run automated setup? (y/n): ").lower()
    return confirm.startswith("y")


# non-interactive config
def env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


def get_config_noninteractive():
    db_host = env("SE_DB_HOST", "mysql")
    db_port = env("SE_DB_PORT", "3306")
    db_name = env("SE_DB_NAME", "scoring_engine")
    db_user = env("SE_DB_USER", "se_user")
    db_pw = env("SE_DB_PASSWORD", "CHANGEME")
    db_uri = f"mysql://{db_user}:{db_pw}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

    redis_host = env("SE_REDIS_HOST", "redis")
    redis_port = env("SE_REDIS_PORT", "6379")
    redis_pw = env("SE_REDIS_PASSWORD", "")

    comp_name = env("SE_COMP_NAME", "Integration Test")
    scoring_interval = env("SE_SCORING_INTERVAL", "300")

    admin_user = env("SE_ADMIN_USER", "admin")
    admin_pw = env("SE_ADMIN_PASSWORD", "admin")

    cfg = {"deployment_mode": "docker"}
    cfg["engine"] = {}
    cfg["database"] = {
        "type": "mysql", "host": db_host, "port": db_port, "name": db_name,
        "user": db_user, "password": db_pw, "uri": db_uri,
    }
    cfg["redis"] = {
        "cache_type": "redis", "redis_host": redis_host,
        "redis_port": redis_port, "redis_password": redis_pw,
    }
    cfg["competition"] = {"competition_name": comp_name, "scoring_interval": scoring_interval}
    cfg["admin"] = {"admin_username": admin_user, "admin_password": admin_pw}
    cfg["game"] = {"start_time": "", "duration_hours": "8"}
    cfg["teams"] = []
    cfg["services"] = []
    return cfg


# file writers
def write_env(config):
    with open(ENV_FILE, "w") as f:
        f.write(f"DB_URI={config['database']['uri']}\n")
        f.write(f"REDIS_HOST={config['redis']['redis_host']}\n")
        f.write(f"REDIS_PORT={config['redis']['redis_port']}\n")
        if config["redis"]["redis_password"]:
            f.write(f"REDIS_PASSWORD={config['redis']['redis_password']}\n")
        f.write(f"COMP_NAME={config['competition']['competition_name']}\n")
        f.write(f"ADMIN_USER={config['admin']['admin_username']}\n")
    print(f"Created {ENV_FILE}")


def _set_ini_value(text: str, key: str, value: str) -> str:
    """Replace `key = ...` (or `#key = ...`) with `key = value`."""
    pat_active = re.compile(rf"(?m)^\s*{re.escape(key)}\s*=\s*.*$")
    if pat_active.search(text):
        return pat_active.sub(f"{key} = {value}", text)

    pat_commented = re.compile(rf"(?m)^\s*#\s*{re.escape(key)}\s*=\s*.*$")
    if pat_commented.search(text):
        return pat_commented.sub(f"{key} = {value}", text)

    lines = text.splitlines(True)
    for i, line in enumerate(lines):
        if line.strip() == "[OPTIONS]":
            lines.insert(i + 1, f"{key} = {value}\n")
            return "".join(lines)

    return text + f"\n{key} = {value}\n"


def write_engine_conf(config, out_path: Path):
    if not ENGINE_CONF_TEMPLATE.exists():
        raise FileNotFoundError(f"Template file not found: {ENGINE_CONF_TEMPLATE}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = ENGINE_CONF_TEMPLATE.read_text()
    text = _set_ini_value(text, "db_uri", config["database"]["uri"])
    text = _set_ini_value(text, "redis_host", config["redis"]["redis_host"])
    text = _set_ini_value(text, "redis_port", str(config["redis"]["redis_port"]))
    text = _set_ini_value(text, "redis_password", config["redis"].get("redis_password", ""))
    text = _set_ini_value(text, "target_round_time", config["competition"]["scoring_interval"])

    if config.get("engine") and "agent_psk" in config["engine"]:
        text = _set_ini_value(text, "agent_psk", config["engine"]["agent_psk"])

    out_path.write_text(text)
    print(f"Created {out_path}")


def write_compose_override():
    """Generate docker-compose.override.yml to mount the installer-generated config into each service.

    Keeps the base docker-compose.yml working for users who haven't run the installer,
    while ensuring installer users get their custom engine.conf automatically.
    """
    content = """\
# Generated by setup_installer.py — do not edit manually.
# Mounts the installer-generated engine.conf.inc into each service.
services:
  bootstrap:
    volumes:
      - ./docker/engine.conf.inc:/app/engine.conf:ro
  engine:
    volumes:
      - ./docker/engine.conf.inc:/app/engine.conf:ro
  worker:
    volumes:
      - ./docker/engine.conf.inc:/app/engine.conf:ro
  web:
    volumes:
      - ./docker/engine.conf.inc:/app/engine.conf:ro
"""
    with open(COMPOSE_OVERRIDE, "w") as f:
        f.write(content)
    print(f"Created {COMPOSE_OVERRIDE}")


def write_competition_yaml(config):
    """Generate bin/competition.yaml from wizard-collected config."""
    teams_out = []

    # White team — uses the admin account created in step 5
    teams_out.append({
        "name": "WhiteTeam",
        "color": "White",
        "users": [{"username": config["admin"]["admin_username"],
                   "password": config["admin"]["admin_password"]}],
    })

    # Red team — placeholder; credentials can be changed in the admin UI after setup
    teams_out.append({
        "name": "RedTeam",
        "color": "Red",
        "users": [{"username": "redteamuser", "password": "changeme"}],
    })

    # Blue teams
    for team in config["teams"]:
        team_services = []
        for svc in config["services"]:
            team_host = svc["team_hosts"][team["name"]]

            # Resolve __TEAM_HOST__ placeholder used by HTTP/HTTPS vhost property
            resolved_props = [
                {"name": p["name"], "value": team_host if p["value"] == "__TEAM_HOST__" else p["value"]}
                for p in svc["properties"]
            ]

            environment = {"matching_content": svc["matching_content"]}
            if resolved_props:
                environment["properties"] = resolved_props

            entry = {
                "name": svc["name"],
                "check_name": svc["check_name"],
                "host": team_host,
                "port": svc["port"],
                "points": svc["points"],
                "environments": [environment],
            }
            if svc["accounts"]:
                entry["accounts"] = svc["accounts"]

            team_services.append(entry)

        teams_out.append({
            "name": team["name"],
            "color": "Blue",
            "users": [{"username": team["username"], "password": team["password"]}],
            "services": team_services,
        })

    data = {"teams": teams_out, "flags": []}
    COMPETITION_YAML.parent.mkdir(parents=True, exist_ok=True)
    with open(COMPETITION_YAML, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"Created {COMPETITION_YAML}")


def safe_cleanup(remove_competition_yaml=False):
    try:
        if DOCKER_ENGINE_CONF.exists():
            DOCKER_ENGINE_CONF.unlink()
        if ENV_FILE.exists():
            ENV_FILE.unlink()
        if COMPOSE_OVERRIDE.exists():
            COMPOSE_OVERRIDE.unlink()
        if remove_competition_yaml and COMPETITION_YAML.exists():
            COMPETITION_YAML.unlink()
    except Exception:
        pass
    docker_compose_down_volumes()
    print("Rollback complete.")


def parse_args():
    p = argparse.ArgumentParser(description="Scoring Engine Setup Wizard")
    p.add_argument("--non-interactive", action="store_true",
                   help="Read inputs from env vars and write config files only.")
    p.add_argument("--no-run", action="store_true",
                   help="Write config files only; do not start docker or bootstrap.")
    return p.parse_args()


# main flow
def main():
    args = parse_args()

    # non-interactive mode for CI/testing — skips team/service prompts, uses existing competition.yaml
    if args.non_interactive:
        config = get_config_noninteractive()
        DOCKER_ENGINE_CONF.parent.mkdir(parents=True, exist_ok=True)
        write_engine_conf(config, DOCKER_ENGINE_CONF)
        write_env(config)
        write_compose_override()
        print("\nNon-interactive mode complete (generated config files only).")
        print(f" - {DOCKER_ENGINE_CONF}")
        print(f" - {ENV_FILE}")
        print(f" - {COMPOSE_OVERRIDE}")
        return

    clear()
    print("""
Welcome to the Scoring Engine Setup Wizard
-------------------------------------------------------
This wizard will configure a new competition:
  1. Database & Redis connection
  2. Competition name and scoring interval
  3. Game duration (optional scheduled start)
  4. Admin account
  5. Blue teams
  6. Services to score

Press Enter to accept the default values shown in [brackets].
""")

    # Check if a competition is already configured
    if check_already_configured():
        print("WARNING: A competition is already configured in bin/competition.yaml.")
        overwrite = input("Overwrite existing configuration? (y/n): ").lower()
        if not overwrite.startswith("y"):
            print("Setup cancelled. Existing configuration was not changed.")
            return

    require_docker()

    config = {"deployment_mode": "docker"}
    config["engine"] = get_engine_settings()
    config["database"] = get_db_config()
    config["redis"] = get_redis_config()
    config["competition"] = get_competition_info()
    config["game"] = get_game_duration_config()
    config["admin"] = get_admin_info()
    config["teams"] = get_teams_config()
    config["services"] = get_services_config(config["teams"])

    if not confirm_summary(config):
        print("Setup cancelled. No files were generated.")
        return

    # Write all config files — competition.yaml must exist before bootstrap runs
    yaml_written = False
    try:
        DOCKER_ENGINE_CONF.parent.mkdir(parents=True, exist_ok=True)
        write_engine_conf(config, DOCKER_ENGINE_CONF)
        write_env(config)
        write_compose_override()
        write_competition_yaml(config)
        yaml_written = True
    except Exception as e:
        sys.exit(f"Failed to write configuration files: {e}")

    # Print game schedule info
    game = config["game"]
    if game["start_time"]:
        print(f"\nGame scheduled to start: {game['start_time']}")
    print(f"Game duration: {game['duration_hours']} hours")
    print("Note: Start the engine at the scheduled time — automatic scheduling is not yet supported.\n")

    docker_compose_up(["mysql", "redis"])

    print("→ Waiting for MySQL to be reachable inside Docker network...")
    ok, err = wait_for_tcp_inside_network(config["database"]["host"], config["database"]["port"], timeout_s=120)
    if not ok:
        print(f"MySQL not reachable: {err}")
        if input("Rollback? (y/n): ").lower().startswith("y"):
            safe_cleanup(remove_competition_yaml=yaml_written)
        sys.exit("Exiting setup.")

    print("→ Waiting for Redis to be reachable inside Docker network...")
    ok, err = wait_for_tcp_inside_network(config["redis"]["redis_host"], config["redis"]["redis_port"], timeout_s=120)
    if not ok:
        print(f"Redis not reachable: {err}")
        if input("Rollback? (y/n): ").lower().startswith("y"):
            safe_cleanup(remove_competition_yaml=yaml_written)
        sys.exit("Exiting setup.")

    print("→ Validating database credentials...")
    db_ok, db_err = test_db_uri_inside_docker(config["database"]["uri"], timeout_s=90)
    if not db_ok:
        print(f"\nDatabase credential test failed.\n{db_err}")
        if input("Rollback? (y/n): ").lower().startswith("y"):
            safe_cleanup(remove_competition_yaml=yaml_written)
        sys.exit("Exiting setup.")

    print("→ Validating Redis connectivity...")
    r_ok, r_err = test_redis_inside_docker(
        config["redis"]["redis_host"],
        config["redis"]["redis_port"],
        config["redis"].get("redis_password", "")
    )
    if not r_ok:
        print(f"\nRedis test failed.\n{r_err}")
        if input("Rollback? (y/n): ").lower().startswith("y"):
            safe_cleanup(remove_competition_yaml=yaml_written)
        sys.exit("Exiting setup.")

    boot_ok, boot_err = run_bootstrap_once()
    if not boot_ok:
        print(f"\nBootstrap failed (schema init / seed).\n{boot_err}")
        if input("Rollback? (y/n): ").lower().startswith("y"):
            safe_cleanup(remove_competition_yaml=yaml_written)
        sys.exit("Exiting setup.")

    print("\nSetup complete!")
    print(f"Generated files:\n - {DOCKER_ENGINE_CONF}\n - {ENV_FILE}\n - {COMPOSE_OVERRIDE}\n - {COMPETITION_YAML}")
    print("\nStart the full stack:")
    print("  docker compose up -d")
    print("\n(If already running) check status:")
    print("  docker compose ps")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSetup aborted by user.")
