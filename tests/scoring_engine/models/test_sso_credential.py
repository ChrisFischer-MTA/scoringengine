from scoring_engine.models.sso_credential import SSOCredential
from scoring_engine.models.team import Team

from tests.scoring_engine.unit_test import UnitTest


class TestSSOCredential(UnitTest):

    def test_init_sso_credential(self):
        cred = SSOCredential(username="admin", password="testpass")
        assert cred.id is None
        assert cred.username == "admin"
        assert cred.password == "testpass"
        assert cred.team is None
        assert cred.team_id is None

    def test_sso_credential_with_team(self):
        team = Team(name="Blue Team 1", color="Blue")
        self.session.add(team)
        self.session.commit()

        cred = SSOCredential(username="admin", password="testpass", team=team)
        self.session.add(cred)
        self.session.commit()

        assert cred.id is not None
        assert cred.team == team
        assert cred.team_id == team.id

    def test_team_sso_credentials_relationship(self):
        team = Team(name="Blue Team 1", color="Blue")
        self.session.add(team)
        self.session.commit()

        cred1 = SSOCredential(username="admin", password="pass1", team=team)
        cred2 = SSOCredential(username="sysadmin", password="pass2", team=team)
        self.session.add(cred1)
        self.session.add(cred2)
        self.session.commit()

        assert len(team.sso_credentials) == 2
        usernames = [c.username for c in team.sso_credentials]
        assert "admin" in usernames
        assert "sysadmin" in usernames

    def test_multiple_teams_same_username(self):
        team1 = Team(name="Blue Team 1", color="Blue")
        team2 = Team(name="Blue Team 2", color="Blue")
        self.session.add(team1)
        self.session.add(team2)
        self.session.commit()

        cred1 = SSOCredential(username="admin", password="team1pass", team=team1)
        cred2 = SSOCredential(username="admin", password="team2pass", team=team2)
        self.session.add(cred1)
        self.session.add(cred2)
        self.session.commit()

        assert cred1.username == cred2.username
        assert cred1.password != cred2.password
        assert cred1.team_id != cred2.team_id
