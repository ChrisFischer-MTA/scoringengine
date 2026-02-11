import mock
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded

from scoring_engine.engine.job import Job
from scoring_engine.engine.execute_command import execute_command


class TestWorker(object):

    def test_basic_run(self):
        job = Job(environment_id="12345", command="echo 'HELLO'")
        task = execute_command.run(job)
        assert task['errored_out'] is False
        assert task['output'] == 'HELLO\n'

    @mock.patch('scoring_engine.engine.execute_command.execute_command.retry')
    def test_timed_out(self, mock_retry):
        import subprocess
        subprocess.run = mock.Mock(side_effect=SoftTimeLimitExceeded)

        job = Job(environment_id="12345", command="echo 'HELLO'")
        task = execute_command.run(job)
        
        assert task['errored_out'] is True
        
        # FIRST RETRY
        execute_command.request.retries = 0
        execute_command.run(job)

        expected_countdown = 30 * (2 ** 0)
        mock_retry.assert_called_with(countdown=expected_countdown, max_retries=3)

        # SECOND RETRY
        mock_retry.reset_mock()
        execute_command.request.retries = 1
        execute_command.run(job)

        expected_countdown = 30 * (2 ** 1)
        mock_retry.assert_called_with(countdown=expected_countdown, max_retries=3)