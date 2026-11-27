"""Guards on the synthesized template: no AWS calls, runs in under a few seconds."""

import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth_stack import AuthStack  # noqa: E402


def template(env_name: str) -> Template:
    app = cdk.App()
    stack = AuthStack(app, "T", env_name=env_name, ses_from_email="sender@example.com",
                      env=cdk.Environment(account="111111111111", region="ap-southeast-2"))
    return Template.from_stack(stack)


def test_client_allows_only_choice_based_auth_and_refresh():
    template("dev").has_resource_properties("AWS::Cognito::UserPoolClient", {
        "ExplicitAuthFlows": ["ALLOW_USER_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        "GenerateSecret": False,
        "AllowedOAuthFlowsUserPoolClient": False,
        "PreventUserExistenceErrors": "ENABLED",
        "EnableTokenRevocation": True,
        "AccessTokenValidity": 15,
    })


def test_pool_is_essentials_admin_only_email_otp():
    template("dev").has_resource_properties("AWS::Cognito::UserPool", {
        "UserPoolTier": "ESSENTIALS",
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        "Policies": Match.object_like({"SignInPolicy": {"AllowedFirstAuthFactors": ["PASSWORD", "EMAIL_OTP"]}}),
        "MfaConfiguration": "OFF",
    })


@pytest.mark.parametrize("env_name, protection, policy", [
    ("dev", "INACTIVE", "Delete"),
    ("prod", "ACTIVE", "Retain"),
])
def test_only_prod_is_protected_from_deletion(env_name, protection, policy):
    t = template(env_name)
    t.has_resource_properties("AWS::Cognito::UserPool", {"DeletionProtection": protection})
    t.has_resource("AWS::Cognito::UserPool", {"DeletionPolicy": policy})
