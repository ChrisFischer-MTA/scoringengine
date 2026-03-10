from scoring_engine.engine.basic_check import BasicCheck, CHECKS_BIN_PATH


class SSHCheck(BasicCheck):
    required_properties = ['commands']
    CMD = CHECKS_BIN_PATH + '/ssh_check {0} {1} {2} {3}'

    def __init__(self, environment):
        super().__init__(environment)
        self._account = self.get_random_account()

    def command_format(self, properties):
        return (
            self.host,
            self.port,
            self._account.username,
            properties['commands']
        )

    def command_env(self):
        return {'SCORING_PASSWORD': self._account.password}
