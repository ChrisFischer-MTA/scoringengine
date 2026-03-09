from datetime import datetime, timedelta

from scoring_engine.engine.basic_check import (
    CHECK_CONNECTION_REFUSED_TEXT,
    CHECK_CONNECTION_TIMEOUT_TEXT,
    CHECK_FAILURE_TEXT,
    CHECK_HOST_UNREACHABLE_TEXT,
    CHECK_SUCCESS_TEXT,
)
from scoring_engine.engine.engine import Engine
from scoring_engine.models.check import Check
from scoring_engine.models.flag import Flag, FlagTypeEnum, Perm, Platform, Solve
from scoring_engine.models.machines import Machine
from scoring_engine.models.round import Round
from scoring_engine.models.service import Service
from scoring_engine.models.team import Team
from tests.scoring_engine.unit_test import UnitTest


class TestUpdateMachineStatusesForRound(UnitTest):
    def setup_method(self):
        super(TestUpdateMachineStatusesForRound, self).setup_method()
        self.engine = Engine(total_rounds=0)

        self.blue_team = Team(name="Blue Team 1", color="Blue")
        self.session.add(self.blue_team)
        self.session.commit()

        now = datetime.utcnow()
        self.round = Round(number=1, round_start=now - timedelta(minutes=5), round_end=now)
        self.session.add(self.round)
        self.session.commit()

        self.service = Service(name="Web", check_name="HTTPCheck", host="10.0.0.1", port=80, team=self.blue_team)
        self.session.add(self.service)
        self.session.commit()

        self.machine = Machine(name="10.0.0.1", team_id=self.blue_team.id, status=Machine.STATUS_UNKNOWN)
        self.session.add(self.machine)
        self.session.commit()

    def _make_check(self, reason):
        check = Check(round_id=self.round.id, service_id=self.service.id, result=False, reason=reason, completed=True)
        self.session.add(check)
        self.session.commit()
        return check

    def _make_flag(self, active=True):
        now = datetime.utcnow()
        flag = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/tmp/flag", "content": "A"},
            start_time=now - timedelta(minutes=10) if active else now - timedelta(hours=2),
            end_time=now + timedelta(minutes=10) if active else now - timedelta(hours=1),
            perm=Perm.user,
            dummy=False,
        )
        self.session.add(flag)
        self.session.commit()
        return flag

    def test_skips_round_with_no_start_time(self):
        self.round.round_start = None
        self.session.commit()

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_UNKNOWN

    def test_skips_round_with_no_end_time(self):
        self.round.round_end = None
        self.session.commit()

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_UNKNOWN

    def test_marks_healthy_when_checks_pass(self):
        check = Check(round_id=self.round.id, service_id=self.service.id, result=True, reason=CHECK_SUCCESS_TEXT, completed=True)
        self.session.add(check)
        self.session.commit()

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_HEALTHY

    def test_marks_offline_when_all_checks_are_connection_failures(self):
        self._make_check(CHECK_HOST_UNREACHABLE_TEXT)

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_OFFLINE

    def test_marks_offline_for_connection_timeout(self):
        self._make_check(CHECK_CONNECTION_TIMEOUT_TEXT)

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_OFFLINE

    def test_marks_offline_for_connection_refused(self):
        self._make_check(CHECK_CONNECTION_REFUSED_TEXT)

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_OFFLINE

    def test_marks_healthy_when_mixed_failures(self):
        # One connection failure, one generic failure → not all are connection failures → healthy
        self._make_check(CHECK_HOST_UNREACHABLE_TEXT)
        self._make_check(CHECK_FAILURE_TEXT)

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_HEALTHY

    def test_marks_compromised_when_active_flag_solved(self):
        flag = self._make_flag(active=True)
        self.session.add(Solve(host="10.0.0.1", team=self.blue_team, flag=flag))
        self.session.commit()

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_COMPROMISED

    def test_compromised_takes_priority_over_offline(self):
        # All checks are connection failures AND machine is compromised → compromised wins
        self._make_check(CHECK_HOST_UNREACHABLE_TEXT)
        flag = self._make_flag(active=True)
        self.session.add(Solve(host="10.0.0.1", team=self.blue_team, flag=flag))
        self.session.commit()

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_COMPROMISED

    def test_does_not_mark_compromised_for_expired_flag(self):
        flag = self._make_flag(active=False)
        self.session.add(Solve(host="10.0.0.1", team=self.blue_team, flag=flag))
        self.session.commit()

        check = Check(round_id=self.round.id, service_id=self.service.id, result=True, reason=CHECK_SUCCESS_TEXT, completed=True)
        self.session.add(check)
        self.session.commit()

        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_HEALTHY

    def test_marks_healthy_when_no_checks_recorded(self):
        # No checks for this round — no connection failures → falls through to healthy
        self.engine._update_machine_statuses_for_round(self.round)
        self.session.commit()

        self.session.refresh(self.machine)
        assert self.machine.status == Machine.STATUS_HEALTHY
