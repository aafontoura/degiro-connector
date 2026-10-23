"""H-3: the OTP is a strict six-character ASCII-digit string."""

import pydantic
import pytest

from degiro_connector.core.models.model_session import ModelSession
from degiro_connector.hardened import DegiroError, ErrorCode, OtpCredentials
from degiro_connector.trading.actions.action_connect import ActionConnect
from degiro_connector.trading.models.credentials import Credentials

from .conftest import LOGIN_OK, PASSWORD, SESSION_ID, USERNAME, FakeAdapter


@pytest.mark.parametrize("otp", ["012345", "000000", "999999"])
def test_valid_codes_are_kept_exactly(otp):
    assert OtpCredentials(USERNAME, PASSWORD, otp).take() == (USERNAME, PASSWORD, otp)


@pytest.mark.parametrize(
    "otp",
    [12345, 0, None, True, "12345", "1234567", "12a456", " 12345", "12345 ", "１２３４５６", "٠١٢٣٤٥", ""],
)
def test_anything_else_is_refused(otp):
    with pytest.raises(DegiroError) as raised:
        OtpCredentials(USERNAME, PASSWORD, otp)
    assert raised.value.code is ErrorCode.INVALID_CREDENTIALS


@pytest.mark.parametrize("otp", ["012345", "000000"])
def test_login_sends_the_code_as_typed_to_the_totp_endpoint(client, adapter, otp):
    adapter.queue(LOGIN_OK)
    client.login(OtpCredentials(USERNAME, PASSWORD, otp))
    sent = adapter.sent[0]
    assert sent.request.method == "POST"
    assert sent.request.url == "https://trader.degiro.nl/login/secure/login/totp"
    assert sent.json["oneTimePassword"] == otp
    assert isinstance(sent.json["oneTimePassword"], str)


def test_upstream_credentials_keep_the_leading_zero():
    credentials = Credentials(username=USERNAME, password=PASSWORD, one_time_password="012345")
    assert credentials.one_time_password == "012345"


@pytest.mark.parametrize("otp", [12345, "12345", "abcdef"])
def test_upstream_credentials_refuse_non_strings_and_bad_shapes(otp):
    with pytest.raises(pydantic.ValidationError):
        Credentials(username=USERNAME, password=PASSWORD, one_time_password=otp)


def test_upstream_credentials_refuse_both_otp_and_totp_secret():
    with pytest.raises(pydantic.ValidationError):
        Credentials(
            username=USERNAME, password=PASSWORD, one_time_password="012345", totp_secret_key="S"
        )


def test_upstream_empty_secret_fields_mean_absent():
    credentials = Credentials(
        username=USERNAME, password=PASSWORD, totp_secret_key="", in_app_token=""
    )
    assert credentials.totp_secret_key is None
    assert credentials.in_app_token is None


def test_upstream_connect_sends_all_zero_code_to_the_totp_endpoint():
    adapter = FakeAdapter().queue(LOGIN_OK)
    session = ModelSession.build_session()
    session.mount("https://", adapter)
    credentials = Credentials(username=USERNAME, password=PASSWORD, one_time_password="000000")

    assert ActionConnect.get_session_id(credentials=credentials, session=session) == SESSION_ID
    sent = adapter.sent[0]
    assert sent.request.url.endswith("/login/secure/login/totp")
    assert sent.json["oneTimePassword"] == "000000"
