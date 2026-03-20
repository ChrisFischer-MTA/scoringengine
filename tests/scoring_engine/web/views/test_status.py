"""Tests for Status web view authorization and setting-based access."""
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team
from scoring_engine.models.user import User
from tests.scoring_engine.web.web_test import WebTest


class TestStatus(WebTest):
    def setup_method(self):
        super(TestStatus, self).setup_method()
        Setting.clear_cache()

        self.white_team = Team(name="White Team", color="White")
        self.red_team = Team(name="Red Team", color="Red")
        self.blue_team = Team(name="Blue Team", color="Blue")

        self.session.add_all([self.white_team, self.red_team, self.blue_team])
        self.session.commit()

        self.white_user = User(username="whiteuser", password="pass", team=self.white_team)
        self.red_user = User(username="reduser", password="pass", team=self.red_team)
        self.blue_user = User(username="blueuser", password="pass", team=self.blue_team)

        self.session.add_all([self.white_user, self.red_user, self.blue_user])
        self.session.commit()

    def test_status_requires_auth(self):
        resp = self.client.get("/status")
        assert resp.status_code == 302
        assert "/login?" in resp.location

    def test_status_white_team_can_access(self):
        self.login("whiteuser", "pass")
        resp = self.client.get("/status")
        assert resp.status_code == 200

    def test_status_red_team_can_access(self):
        self.login("reduser", "pass")
        resp = self.client.get("/status")
        assert resp.status_code == 200

    def test_status_blue_team_can_access_when_setting_true(self):
        setting = Setting.get_setting("blue_team_view_status_page")
        setting.value = True
        self.session.add(setting)
        self.session.commit()
        Setting.clear_cache("blue_team_view_status_page")

        self.login("blueuser", "pass")
        resp = self.client.get("/status")
        assert resp.status_code == 200

    def test_status_blue_team_redirected_when_setting_false(self):
        setting = Setting.get_setting("blue_team_view_status_page")
        setting.value = False
        self.session.add(setting)
        self.session.commit()
        Setting.clear_cache("blue_team_view_status_page")

        self.login("blueuser", "pass")
        resp = self.client.get("/status")

        assert resp.status_code == 302
        assert "/unauthorized" in resp.location
