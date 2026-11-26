"""Cognito user pool + app client for one environment (dev / staging / prod).

Mirrors the hand-made Level 2 dev pool, with the Step 12 hardening built in.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from constructs import Construct


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str,
                 ses_from_email: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        prod = env_name == "prod"

        pool = cognito.UserPool(
            self, "Drivers",
            user_pool_name=f"setuhaul-{env_name}",
            feature_plan=cognito.FeaturePlan.ESSENTIALS,      # email OTP needs Essentials (D1)
            self_sign_up_enabled=False,                       # ops create drivers; no public sign-up
            sign_in_aliases=cognito.SignInAliases(email=True),
            sign_in_case_sensitive=False,
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            # PASSWORD can't be turned off (Cognito requires it); drivers never get one (12d).
            sign_in_policy=cognito.SignInPolicy(
                allowed_first_auth_factors=cognito.AllowedFirstAuthFactors(password=True, email_otp=True),
            ),
            mfa=cognito.Mfa.OFF,                              # OTP sign-in doesn't work with required MFA
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            email=cognito.UserPoolEmail.with_ses(from_email=ses_from_email, ses_region=self.region),
            deletion_protection=prod,
            removal_policy=RemovalPolicy.RETAIN if prod else RemovalPolicy.DESTROY,
        )

        client = pool.add_client(
            "Front",
            user_pool_client_name=f"setuhaul-front-{env_name}",
            generate_secret=False,                            # public client: the UI can't keep a secret
            auth_flows=cognito.AuthFlow(user=True),           # USER_AUTH only (+ refresh, always on)
            disable_o_auth=True,                              # no hosted UI / redirects: we use InitiateAuth
            prevent_user_existence_errors=True,
            enable_token_revocation=True,
            access_token_validity=Duration.minutes(15),       # 12d: short life limits stolen tokens
            id_token_validity=Duration.minutes(15),
            refresh_token_validity=Duration.days(5),
        )

        # The three values the API needs in its environment (COGNITO_*).
        CfnOutput(self, "CognitoRegion", value=self.region)
        CfnOutput(self, "CognitoUserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "CognitoClientId", value=client.user_pool_client_id)
