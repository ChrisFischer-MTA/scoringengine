from datetime import datetime

from sqlalchemy.sql import func

from scoring_engine.db import db
from scoring_engine.models.machines import Machine
from scoring_engine.models.service import Service


def sync_machines_from_services():

    distinct_hosts = (
        db.session.query(
            Service.team_id,
            func.lower(func.trim(Service.host)).label("host"),
        )
        .filter(Service.team_id.isnot(None))
        .filter(Service.host.isnot(None))
        .filter(func.trim(Service.host) != "")
        .filter(Service.check_name == "AgentCheck")  # key change
        .distinct()
        .all()
    )

    existing_pairs = {
        (team_id, (name or "").strip().lower())
        for team_id, name in db.session.query(Machine.team_id, Machine.name).all()
    }

    now = datetime.utcnow()
    created = 0

    for team_id, host in distinct_hosts:
        key = (team_id, host)
        if key in existing_pairs:
            continue

        db.session.add(
            Machine(
                team_id=team_id,
                name=host,
                status=Machine.STATUS_UNKNOWN,
                last_check_in_at=None,
                last_status_change_at=now,
            )
        )
        existing_pairs.add(key)
        created += 1

    if created:
        db.session.commit()

    return {
        "scanned": len(distinct_hosts),
        "created": created,
        "existing": len(distinct_hosts) - created,
    }
