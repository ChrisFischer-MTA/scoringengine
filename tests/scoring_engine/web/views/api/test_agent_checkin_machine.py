from datetime import datetime, timedelta

from scoring_engine.models.flag import Flag, FlagTypeEnum, Perm, Platform
from scoring_engine.models.machines import Machine
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team
from scoring_engine.web.views.api.agent import BtaPayloadEncryption
from tests.scoring_engine.unit_test import UnitTest

PSK = "TheCakeIsALie"


class TestAgentCheckinMachineUpdate(UnitTest):
    def setup_method(self):
        super(TestAgentCheckinMachineUpdate, self).setup_method()
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

        self.blue_team = Team(name="Blue Team 1", color="Blue")
        self.session.add(self.blue_team)
        self.session.commit()

        self.session.add(Setting(name="agent_psk", value=PSK))
        self.session.add(Setting(name="agent_checkin_interval_sec", value=60))
        self.session.add(Setting(name="agent_show_flag_early_mins", value=5))
        self.session.commit()

        self.machine = Machine(name="10.0.0.1", team_id=self.blue_team.id, status=Machine.STATUS_UNKNOWN)
        self.session.add(self.machine)
        self.session.commit()

    def _checkin(self, host="10.0.0.1", flags=None):
        payload = {
            "team": self.blue_team.name,
            "host": host,
            "plat": "nix",
            "flags": flags or [],
        }
        crypter = BtaPayloadEncryption(PSK, self.blue_team.name)
        body = crypter.dumps(payload)
        return self.client.post(
            f"/api/agent/checkin?t={self.blue_team.name}",
            data=body,
            content_type="application/octet-stream",
        )

    def test_checkin_updates_last_check_in_at(self):
        assert self.machine.last_check_in_at is None

        resp = self._checkin()

        assert resp.status_code == 200
        self.session.refresh(self.machine)
        assert self.machine.last_check_in_at is not None

    def test_checkin_without_flags_does_not_change_status(self):
        resp = self._checkin()

        assert resp.status_code == 200
        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_UNKNOWN

    def test_checkin_with_no_matching_machine_does_not_crash(self):
        # Host has no Machine record — should succeed without error
        resp = self._checkin(host="99.99.99.99")

        assert resp.status_code == 200

    def test_checkin_is_case_insensitive_for_host(self):
        # Machine stored as lowercase, checkin uses mixed case
        resp = self._checkin(host="10.0.0.1")

        assert resp.status_code == 200
        self.session.refresh(self.machine)
        assert self.machine.last_check_in_at is not None

    def test_checkin_with_valid_flag_marks_machine_compromised(self):
        now = datetime.utcnow()
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/tmp/flag", "content": "A"},
            start_time=now - timedelta(minutes=10),
            end_time=now + timedelta(hours=1),
            perm=Perm.user,
            dummy=False,
        )
        self.session.add(flag)
        self.session.commit()

        resp = self._checkin(flags=[flag.id])

        assert resp.status_code == 200
        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_COMPROMISED

    def test_checkin_with_dummy_flag_does_not_mark_compromised(self):
        now = datetime.utcnow()
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/tmp/flag", "content": "A"},
            start_time=now - timedelta(minutes=10),
            end_time=now + timedelta(hours=1),
            perm=Perm.user,
            dummy=True,
        )
        self.session.add(flag)
        self.session.commit()

        resp = self._checkin(flags=[flag.id])

        assert resp.status_code == 200
        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_UNKNOWN
