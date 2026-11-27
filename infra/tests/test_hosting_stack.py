"""Guards on the hosting template: no AWS calls."""

import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hosting_stack import HostingStack  # noqa: E402


def template(env_name: str) -> Template:
    app = cdk.App()
    stack = HostingStack(app, "H", env_name=env_name,
                         env=cdk.Environment(account="111111111111", region="ap-southeast-2"))
    return Template.from_stack(stack)


def test_image_repo_is_immutable_and_scanned():
    template("dev").has_resource_properties("AWS::ECR::Repository", {
        "RepositoryName": "setuhaul-dev",
        "ImageTagMutability": "IMMUTABLE",
        "ImageScanningConfiguration": {"ScanOnPush": True},
    })


def test_only_prod_keeps_its_images():
    template("prod").has_resource("AWS::ECR::Repository", {"DeletionPolicy": "Retain"})
    template("dev").has_resource("AWS::ECR::Repository", {"DeletionPolicy": "Delete"})