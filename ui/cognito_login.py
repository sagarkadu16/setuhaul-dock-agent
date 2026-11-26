"""Level 2: Cognito passwordless email-OTP login, used by the Streamlit UI.

Runs on the Streamlit server. Needs no AWS credentials: InitiateAuth and
RespondToAuthChallenge are public-client calls, so requests are sent unsigned.
"""

from dataclasses import dataclass

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import logging

_CONFIG = Config(
    signature_version=UNSIGNED,
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 2, "mode": "standard"},
)

WRONG_CODE = "Wrong or expired code. Check your email, or request a new code."
_MESSAGES = {
    "CodeMismatchException": WRONG_CODE,
    "ExpiredCodeException": WRONG_CODE,
    "NotAuthorizedException": "This code can't be used any more. Please request a new one.",
    "TooManyRequestsException": "Too many attempts. Wait a minute and try again.",
    "LimitExceededException": "Too many attempts. Wait a minute and try again.",
}

log = logging.getLogger(__name__)


class LoginError(Exception):
    """The message is safe to show the user."""


@dataclass(frozen=True)
class OtpChallenge:
    email: str
    session: str | None   # None = no code was sent (unknown email). verify() then fails like a wrong code


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int       # seconds


class CognitoLogin:
    def __init__(self, region: str, client_id: str):
        self._client_id = client_id
        # A fresh Session per client: boto3's default session is not thread-safe,
        # and Streamlit serves each browser tab on its own thread.
        self._idp = boto3.session.Session().client("cognito-idp", region_name=region, config=_CONFIG)

    def start(self, email: str) -> OtpChallenge:
        """Ask Cognito to email a code. Same result whether or not the email is registered."""
        try:
            r = self._idp.initiate_auth(
                ClientId=self._client_id,
                AuthFlow="USER_AUTH",
                AuthParameters={"USERNAME": email, "PREFERRED_CHALLENGE": "EMAIL_OTP"},
            )
            if r.get("ChallengeName") == "SELECT_CHALLENGE" and "EMAIL_OTP" in r.get("AvailableChallenges", []):
                r = self._idp.respond_to_auth_challenge(
                    ClientId=self._client_id,
                    ChallengeName="SELECT_CHALLENGE",
                    Session=r["Session"],
                    ChallengeResponses={"USERNAME": email, "ANSWER": "EMAIL_OTP"},
                )
        except (ClientError, BotoCoreError) as e:
            raise LoginError(_message(e)) from e
        if r.get("ChallengeName") != "EMAIL_OTP":
            return OtpChallenge(email=email, session=None)   # don't reveal that the email is unknown
        return OtpChallenge(email=email, session=r["Session"])

    def verify(self, challenge: OtpChallenge, code: str) -> Tokens:
        """Exchange the emailed code for tokens."""
        if challenge.session is None:
            raise LoginError(WRONG_CODE)
        try:
            r = self._idp.respond_to_auth_challenge(
                ClientId=self._client_id,
                ChallengeName="EMAIL_OTP",
                Session=challenge.session,
                ChallengeResponses={"USERNAME": challenge.email, "EMAIL_OTP_CODE": code.strip()},
            )
        except (ClientError, BotoCoreError) as e:
            raise LoginError(_message(e)) from e
        result = r.get("AuthenticationResult")
        if not result:
            raise LoginError("Login could not be completed. Please try again.")
        return Tokens(result["AccessToken"], result["RefreshToken"], result["ExpiresIn"])
    
    def refresh(self, refresh_token: str) -> str:
        """A new access token from the refresh token. No email, no code."""
        try:
            r = self._idp.initiate_auth(
                ClientId=self._client_id,
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters={"REFRESH_TOKEN": refresh_token},
            )
        except (ClientError, BotoCoreError) as e:
            raise LoginError(_message(e)) from e
        return r["AuthenticationResult"]["AccessToken"]
    
    def revoke(self, refresh_token: str) -> None:
        """End this login at Cognito. Best effort: never blocks the user from logging out."""
        try:
            self._idp.revoke_token(Token=refresh_token, ClientId=self._client_id)
        except (ClientError, BotoCoreError) as e:
            log.warning("Cognito token revocation failed: %s", type(e).__name__)


def _message(e: Exception) -> str:
    if isinstance(e, ClientError):
        return _MESSAGES.get(e.response["Error"]["Code"], "Login failed. Please try again.")
    return "Can't reach the login service. Check your connection and try again."


