from scoring_engine.models.team import Team
from tests.scoring_engine.web.web_test import WebTest


class TestWelcome(WebTest):

    def setup_method(self):
        super(TestWelcome, self).setup_method()
        self.create_default_user()
        # welcome.py redirects to /setup when no blue teams exist;
        # create one so the tests reach render_template as expected.
        blue_team = Team(name="Blue Team", color="Blue")
        self.session.add(blue_team)
        self.session.commit()
        self.welcome_content = 'example welcome content <br>here'

    def test_home(self):
        resp = self.client.get('/')
        assert self.mock_obj.call_args == self.build_args('welcome.html', welcome_content=self.welcome_content)
        assert resp.status_code == 200

    def test_home_index(self):
        resp = self.client.get('/index')
        assert self.mock_obj.call_args == self.build_args('welcome.html', welcome_content=self.welcome_content)
        assert resp.status_code == 200
