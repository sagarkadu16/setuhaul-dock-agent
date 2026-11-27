"""CDK entry point. From infra/:  SES_FROM_EMAIL=... npx aws-cdk synth -c env=dev"""

import os

import aws_cdk as cdk

from auth_stack import AuthStack
from hosting_stack import HostingStack

ENVIRONMENTS = ("dev", "staging", "prod")
REGION = "ap-southeast-2"        # the org SCP denies every other region

app = cdk.App()
env_name = app.node.try_get_context("env") or "dev"
if env_name not in ENVIRONMENTS:
    raise ValueError(f"-c env must be one of {ENVIRONMENTS}, got {env_name!r}")

ses_from_email = os.environ.get("SES_FROM_EMAIL", "")   # verified SES sender; kept out of git
if not ses_from_email:
    raise ValueError("Set SES_FROM_EMAIL to a verified SES identity in " + REGION)

AuthStack(
    app, f"SetuhaulAuth-{env_name}",
    env_name=env_name,
    ses_from_email=ses_from_email,
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=REGION),
)
cdk.Tags.of(app).add("project", "setuhaul")
cdk.Tags.of(app).add("env", env_name)
app.synth()
