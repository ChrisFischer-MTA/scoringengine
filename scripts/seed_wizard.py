#!/usr/bin/env python3
"""
Seed the scoring engine wizard via POST /api/setup.
Mirrors the bracket-notation form the wizard submits.

Usage:
    python scripts/seed_wizard.py [BASE_URL]

    BASE_URL defaults to http://198.18.6.47

Credentials are loaded from scripts/seed_config.py (gitignored).
Copy scripts/seed_config.example.py to scripts/seed_config.py and fill in values.
"""

import sys
import requests

BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://198.18.6.47"

try:
    from seed_config import (
        COMPETITION_NAME,
        SCORING_INTERVAL,
        ADMIN_USERNAME,
        ADMIN_PASSWORD,
        RED_TEAM_USERNAME,
        RED_TEAM_PASSWORD,
        TEAMS,
        SERVICES,
        FLAGS,
    )
except ImportError:
    print("ERROR: scripts/seed_config.py not found.")
    print("Copy scripts/seed_config.example.py to scripts/seed_config.py and fill in your values.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Build bracket-notation form data
# ---------------------------------------------------------------------------

def build_form():
    data = []

    data.append(("competition_name", COMPETITION_NAME))
    data.append(("scoring_interval", SCORING_INTERVAL))
    data.append(("admin_username", ADMIN_USERNAME))
    data.append(("admin_password", ADMIN_PASSWORD))
    data.append(("red_team_username", RED_TEAM_USERNAME))
    data.append(("red_team_password", RED_TEAM_PASSWORD))

    for i, team in enumerate(TEAMS):
        data.append((f"teams[{i}][name]", team["name"]))
        data.append((f"teams[{i}][username]", team["username"]))
        data.append((f"teams[{i}][password]", team["password"]))

    for si, svc in enumerate(SERVICES):
        data.append((f"services[{si}][name]", svc["name"]))
        data.append((f"services[{si}][check_name]", svc["check_name"]))
        data.append((f"services[{si}][port]", svc["port"]))
        data.append((f"services[{si}][points]", svc["points"]))
        data.append((f"services[{si}][matching_content]", svc["matching_content"]))

        for team_name, host in svc.get("team_hosts", {}).items():
            data.append((f"services[{si}][team_hosts][{team_name}]", host))

        for ai, acc in enumerate(svc.get("accounts", [])):
            data.append((f"services[{si}][accounts][{ai}][username]", acc["username"]))
            data.append((f"services[{si}][accounts][{ai}][password]", acc["password"]))

        for pi, prop in enumerate(svc.get("properties", [])):
            data.append((f"services[{si}][properties][{pi}][name]", prop["name"]))
            data.append((f"services[{si}][properties][{pi}][value]", prop["value"]))

    for fi, flag in enumerate(FLAGS):
        data.append((f"flags[{fi}][type]", flag["type"]))
        data.append((f"flags[{fi}][platform]", flag["platform"]))
        data.append((f"flags[{fi}][perm]", flag["perm"]))
        data.append((f"flags[{fi}][path]", flag["path"]))
        data.append((f"flags[{fi}][content]", flag["content"]))
        data.append((f"flags[{fi}][start_time]", flag["start_time"]))
        data.append((f"flags[{fi}][rotation_interval]", flag["rotation_interval"]))
        data.append((f"flags[{fi}][num_rotations]", flag["num_rotations"]))
        data.append((f"flags[{fi}][dummy]", flag["dummy"]))

    return data


def main():
    url = f"{BASE_URL}/api/setup"
    print(f"POSTing to {url} ...")
    resp = requests.post(url, data=build_form(), verify=False)
    print(f"Status: {resp.status_code}")
    try:
        print(resp.json())
    except Exception:
        print(resp.text)


if __name__ == "__main__":
    main()
