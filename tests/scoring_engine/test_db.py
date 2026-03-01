import pytest
from unittest.mock import patch, MagicMock

from sqlalchemy.orm import scoped_session
from sqlalchemy.exc import OperationalError, ProgrammingError, InterfaceError

from tests.scoring_engine.unit_test import UnitTest
from scoring_engine.db import (
    db,
    get_readable_error_message,
    test_db_connection as check_db_connection,
    verify_db_ready,
)
from scoring_engine.web import create_app
class TestDB(UnitTest):

    def test_session_type(self):
        assert isinstance(self.session, scoped_session)

    def test_session_available(self):
        assert db.session is not None
        assert self.session is not None

    def test_session_is_db_session(self):
        assert self.session is db.session

def make_operational_error(message):
    return OperationalError(message, params=None, orig=Exception(message))


def make_interface_error(message):
    return InterfaceError(message, params=None, orig=Exception(message))


def make_programming_error(message):
    return ProgrammingError(message, params=None, orig=Exception(message))

@pytest.fixture
def app_ctx():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        yield app

class TestGetReadableErrorMessage:

    @pytest.mark.parametrize("msg", [
        "password authentication failed for user 'scoring'",
        "Access denied for user 'root'@'localhost'",
        "Login failed for user 'sa'",
        "invalid password supplied",
        "FATAL: authentication failed",
    ])
    def test_authentication_errors(self, msg):
        error_type, readable = get_readable_error_message(Exception(msg))
        assert error_type == "authentication"
        assert "authentication failed" in readable.lower()

    @pytest.mark.parametrize("msg", [
        "could not connect to server: Connection refused",
        "Connection refused by host 10.0.0.1",
        "No route to host",
        "Network is unreachable",
        "Host is down",
        "connection timed out",
        "Name or service not known",
        "Unknown host db.example.com",
        "getaddrinfo failed for host 'badhost'",
    ])
    def test_unreachable_errors(self, msg):
        error_type, readable = get_readable_error_message(Exception(msg))
        assert error_type == "unreachable"
        assert "unreachable" in readable.lower()

    @pytest.mark.parametrize("msg", [
        'FATAL: database does not exist',
        "Unknown database 'scoring_engine'",
        "No such file or directory: '/var/lib/mysql/scoring.sock'",
    ])
    def test_database_missing_errors(self, msg):
        error_type, readable = get_readable_error_message(Exception(msg))
        assert error_type == "database_missing"
        assert "database" in readable.lower()

    def test_unknown_error(self):
        error_type, readable = get_readable_error_message(Exception("something unexpected"))
        assert error_type == "unknown"
        assert "something unexpected" in readable

    def test_empty_error_message(self):
        error_type, readable = get_readable_error_message(Exception(""))
        assert error_type == "unknown"

class TestTestDbConnection:

    @patch("sqlalchemy.create_engine")
    def test_successful_connection(self, mock_create_engine):
        mock_conn = MagicMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_create_engine.return_value = mock_engine

        success, msg = check_db_connection("sqlite:///test.db")
        assert success is True
        assert msg == "Connected"

    @patch("sqlalchemy.create_engine")
    def test_auth_failure(self, mock_create_engine):
        mock_create_engine.side_effect = make_operational_error(
            "password authentication failed for user 'scoring'"
        )
        success, msg = check_db_connection("postgresql://bad:pass@localhost/db")
        assert success is False
        assert msg == "Invalid password"

    @patch("sqlalchemy.create_engine")
    def test_host_unreachable(self, mock_create_engine):
        mock_create_engine.side_effect = make_operational_error(
            "could not connect to server: Connection refused"
        )
        success, msg = check_db_connection("postgresql://user:pass@badhost/db")
        assert success is False
        assert msg == "Host unreachable"

    @patch("sqlalchemy.create_engine")
    def test_database_not_found(self, mock_create_engine):
        mock_create_engine.side_effect = make_operational_error(
            'FATAL: database does not exist'
        )
        success, msg = check_db_connection("postgresql://user:pass@localhost/missing")
        assert success is False
        assert msg == "Database not found"

    @patch("sqlalchemy.create_engine")
    def test_interface_error_unreachable(self, mock_create_engine):
        mock_create_engine.side_effect = make_interface_error(
            "connection refused"
        )
        success, msg = check_db_connection("postgresql://user:pass@down/db")
        assert success is False
        assert msg == "Host unreachable"

    @patch("sqlalchemy.create_engine")
    def test_unexpected_exception(self, mock_create_engine):
        mock_create_engine.side_effect = RuntimeError("segfault or something")
        success, msg = check_db_connection("sqlite:///test.db")
        assert success is False
        assert "Connection failed" in msg

    @patch("sqlalchemy.create_engine")
    def test_unknown_operational_error(self, mock_create_engine):
        """OperationalError with a message that doesn't match known patterns."""
        mock_create_engine.side_effect = make_operational_error(
            "disk I/O error"
        )
        success, msg = check_db_connection("sqlite:///test.db")
        assert success is False
        # Falls through to the generic readable message
        assert "Database error" in msg or "disk" in msg.lower()

class TestVerifyDbReady:

    def test_db_ready(self, app_ctx):
        with patch.object(db.session, "get", return_value=MagicMock()):
            assert verify_db_ready() is True

    def test_db_not_ready_operational_error(self, app_ctx):
        with patch.object(
            db.session, "get",
            side_effect=make_operational_error("could not connect to server: Connection refused"),
        ):
            assert verify_db_ready() is False

    def test_db_not_ready_programming_error(self, app_ctx):
        with patch.object(
            db.session, "get",
            side_effect=make_programming_error("relation \"user\" does not exist"),
        ):
            assert verify_db_ready() is False

    def test_db_not_ready_unexpected_error(self, app_ctx):
        with patch.object(
            db.session, "get",
            side_effect=RuntimeError("something unexpected"),
        ):
            assert verify_db_ready() is False

    def test_db_ready_returns_none_user(self, app_ctx):
        with patch.object(db.session, "get", return_value=None):
            assert verify_db_ready() is True
