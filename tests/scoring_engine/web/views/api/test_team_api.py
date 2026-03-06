from scoring_engine.models.service import Service
from scoring_engine.models.team import Team
from scoring_engine.models.user import User
from tests.scoring_engine.unit_test import UnitTest
from scoring_engine.models.flag import Flag, Solve, FlagTypeEnum, Platform, Perm
from scoring_engine.models.machines import Machine
from scoring_engine.models.setting import Setting
from datetime import datetime, timedelta


class TestTeamAPI(UnitTest):
    def setup_method(self):
        super(TestTeamAPI, self).setup_method()
        Setting.clear_cache()
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

        self.white_team = Team(name="White Team", color="White")
        self.red_team = Team(name="Red Team", color="Red")
        self.blue_team_1 = Team(name="Blue Team 1", color="Blue")
        self.blue_team_2 = Team(name="Blue Team 2", color="Blue")
        self.session.add_all([self.white_team, self.red_team, self.blue_team_1, self.blue_team_2])
        self.session.commit()

        self.white_user = User(username="white_user", password="pass", team=self.white_team)
        self.red_user = User(username="red_user", password="pass", team=self.red_team)
        self.blue_user_1 = User(username="blue_user_1", password="pass", team=self.blue_team_1)
        self.blue_user_2 = User(username="blue_user_2", password="pass", team=self.blue_team_2)
        self.session.add_all([self.white_user, self.red_user, self.blue_user_1, self.blue_user_2])
        self.session.commit()

    def login(self, username, password):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def test_team_hosts_returns_distinct_hosts_for_team(self):
        self.session.add_all(
            [
                Service(name="Web", check_name="HTTPCheck", host="host-a", port=80, team=self.blue_team_1),
                Service(name="DNS", check_name="DNSCheck", host="host-b", port=53, team=self.blue_team_1),
                Service(name="SSH", check_name="SSHCheck", host="host-c", port=22, team=self.blue_team_1),
                Service(name="Other Team Host", check_name="HTTPCheck", host="host-z", port=80, team=self.blue_team_2),
            ]
        )
        self.session.commit()

        self.login("blue_user_1", "pass")
        resp = self.client.get(f"/api/team/{self.blue_team_1.id}/hosts")

        assert resp.status_code == 200
        assert resp.json == {"data": [{"host": "host-a"}, {"host": "host-b"}, {"host": "host-c"}]}

    def test_team_hosts_rejects_other_teams(self):
        self.login("blue_user_1", "pass")
        resp = self.client.get(f"/api/team/{self.blue_team_2.id}/hosts")

        assert resp.status_code == 403
        assert resp.json == {"status": "Unauthorized"}

    def test_team_hosts_white_can_access_any_team(self):
        self.session.add(Service(name="Web", check_name="HTTPCheck", host="host-a", port=80, team=self.blue_team_2))
        self.session.commit()

        self.login("white_user", "pass")
        resp = self.client.get(f"/api/team/{self.blue_team_2.id}/hosts")

        assert resp.status_code == 200
        assert resp.json == {"data": [{"host": "host-a"}]}

    def test_team_hosts_red_can_access_any_team(self):
        self.session.add(Service(name="Web", check_name="HTTPCheck", host="host-a", port=80, team=self.blue_team_2))
        self.session.commit()

        self.login("red_user", "pass")
        resp = self.client.get(f"/api/team/{self.blue_team_2.id}/hosts")

        assert resp.status_code == 200
        assert resp.json == {"data": [{"host": "host-a"}]}
    
    def test_machine_history_blue_cannot_access_other_team(self):
        self.login("blue_user_1", "pass")
        resp = self.client.get(f"/api/team/{self.blue_team_2.id}/machine-history")

        assert resp.status_code == 403
        assert resp.json == {"status": "Unauthorized"}

    def test_machine_history_white_can_access_any_team(self):
        self.login("white_user", "pass")
        resp = self.client.get(f"/api/team/{self.blue_team_2.id}/machine-history")

        assert resp.status_code == 200
        assert "data" in resp.json
        assert resp.json["data"]["team_id"] == self.blue_team_2.id

    def test_machine_history_blue_blocked_when_historical_disabled(self):
        setting = Setting.get_setting("blue_team_view_historical_status")
        setting.value = False
        self.session.add(setting)
        self.session.commit()
        Setting.clear_cache("blue_team_view_historical_status")

        self.login("blue_user_1", "pass")
        resp = self.client.get(f"/api/team/{self.blue_team_1.id}/machine-history")

        assert resp.status_code == 403
        assert resp.json == {"status": "Unauthorized"}

    def test_machine_history_returns_rows_columns_and_boolean_history(self):
        m1 = Machine(name="webserver.team1.local", team_id=self.blue_team_1.id)
        m2 = Machine(name="db.team1.local", team_id=self.blue_team_1.id)
        self.session.add_all([m1, m2])

        now = datetime.utcnow()
        newer_start = now - timedelta(minutes=5)
        newer_end = now + timedelta(minutes=5)
        older_start = now - timedelta(minutes=30)
        older_end = now - timedelta(minutes=20)

        f_new = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/tmp/flag1", "content": "A"},
            start_time=newer_start,
            end_time=newer_end,
            perm=Perm.user,
            dummy=False,
        )
        f_old = Flag(
            type=FlagTypeEnum.file,
            platform=Platform.nix,
            data={"path": "/tmp/flag2", "content": "B"},
            start_time=older_start,
            end_time=older_end,
            perm=Perm.user,
            dummy=False,
        )
        self.session.add_all([f_new, f_old])
        self.session.commit()

        self.session.add_all(
            [
                Solve(host="webserver.team1.local", team=self.blue_team_1, flag=f_new),
                Solve(host="db.team1.local", team=self.blue_team_1, flag=f_old),
            ]
        )
        self.session.commit()

        self.login("blue_user_1", "pass")
        resp = self.client.get(f"/api/team/{self.blue_team_1.id}/machine-history")

        assert resp.status_code == 200
        payload = resp.json["data"]

        columns = payload["columns"]
        rows = payload["rows"]

        assert payload["team_id"] == self.blue_team_1.id
        assert len(columns) == 2

        # Endpoint orders newest-first by start_time desc.
        assert columns[0]["start_time"].startswith(newer_start.isoformat()[:16])
        assert columns[1]["start_time"].startswith(older_start.isoformat()[:16])

        by_host = {row["host"]: row for row in rows}
        assert set(by_host.keys()) == {"db.team1.local", "webserver.team1.local"}

        # Newer window at index 0, older at index 1
        assert by_host["webserver.team1.local"]["history"] == [True, False]
        assert by_host["db.team1.local"]["history"] == [False, True]

        for row in rows:
            assert len(row["history"]) == len(columns)
            assert all(isinstance(v, bool) for v in row["history"])
