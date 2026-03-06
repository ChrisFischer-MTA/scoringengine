from scoring_engine.machine_sync import sync_machines_from_services
from scoring_engine.models.machines import Machine
from scoring_engine.models.service import Service
from scoring_engine.models.team import Team
from tests.scoring_engine.unit_test import UnitTest


class TestMachineSync(UnitTest):
    def setup_method(self):
        super(TestMachineSync, self).setup_method()
        self.blue_team = Team(name="Blue Team 1", color="Blue")
        self.blue_team_2 = Team(name="Blue Team 2", color="Blue")
        self.session.add_all([self.blue_team, self.blue_team_2])
        self.session.commit()

    def test_creates_machine_for_agent_check_service(self):
        self.session.add(Service(name="Agent", check_name="AgentCheck", host="10.0.0.1", port=4444, team=self.blue_team))
        self.session.commit()

        result = sync_machines_from_services()

        assert result == {"scanned": 1, "created": 1, "existing": 0}
        machines = self.session.query(Machine).all()
        assert len(machines) == 1
        assert machines[0].name == "10.0.0.1"
        assert machines[0].team_id == self.blue_team.id
        assert machines[0].status == Machine.STATUS_UNKNOWN

    def test_ignores_non_agent_check_services(self):
        self.session.add(Service(name="Web", check_name="HTTPCheck", host="10.0.0.2", port=80, team=self.blue_team))
        self.session.add(Service(name="SSH", check_name="SSHCheck", host="10.0.0.3", port=22, team=self.blue_team))
        self.session.commit()

        result = sync_machines_from_services()

        assert result == {"scanned": 0, "created": 0, "existing": 0}
        assert self.session.query(Machine).count() == 0

    def test_does_not_duplicate_existing_machine(self):
        self.session.add(Service(name="Agent", check_name="AgentCheck", host="10.0.0.1", port=4444, team=self.blue_team))
        self.session.add(Machine(name="10.0.0.1", team_id=self.blue_team.id, status=Machine.STATUS_HEALTHY))
        self.session.commit()

        result = sync_machines_from_services()

        assert result == {"scanned": 1, "created": 0, "existing": 1}
        assert self.session.query(Machine).count() == 1

    def test_creates_machines_for_multiple_teams(self):
        self.session.add(Service(name="Agent", check_name="AgentCheck", host="10.0.0.1", port=4444, team=self.blue_team))
        self.session.add(Service(name="Agent", check_name="AgentCheck", host="10.0.1.1", port=4444, team=self.blue_team_2))
        self.session.commit()

        result = sync_machines_from_services()

        assert result == {"scanned": 2, "created": 2, "existing": 0}
        assert self.session.query(Machine).count() == 2

    def test_deduplicates_multiple_services_on_same_host(self):
        self.session.add(Service(name="Agent 1", check_name="AgentCheck", host="10.0.0.1", port=4444, team=self.blue_team))
        self.session.add(Service(name="Agent 2", check_name="AgentCheck", host="10.0.0.1", port=4445, team=self.blue_team))
        self.session.commit()

        result = sync_machines_from_services()

        assert result == {"scanned": 1, "created": 1, "existing": 0}
        assert self.session.query(Machine).count() == 1

    def test_normalizes_host_case(self):
        self.session.add(Service(name="Agent", check_name="AgentCheck", host="MyHost.Local", port=4444, team=self.blue_team))
        self.session.add(Machine(name="myhost.local", team_id=self.blue_team.id))
        self.session.commit()

        result = sync_machines_from_services()

        assert result == {"scanned": 1, "created": 0, "existing": 1}
        assert self.session.query(Machine).count() == 1

    def test_empty_services_returns_zero_counts(self):
        result = sync_machines_from_services()

        assert result == {"scanned": 0, "created": 0, "existing": 0}
        assert self.session.query(Machine).count() == 0
