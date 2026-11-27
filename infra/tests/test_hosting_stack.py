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

def ingress_rules(t: Template) -> list[dict]:
    inline = [rule for sg in t.find_resources("AWS::EC2::SecurityGroup").values()
              for rule in sg["Properties"].get("SecurityGroupIngress", [])]
    separate = [r["Properties"] for r in t.find_resources("AWS::EC2::SecurityGroupIngress").values()]
    return inline + separate                        # CDK may write a rule either way


def test_only_the_ui_port_is_open_and_only_to_cloudfront():
    rules = ingress_rules(template("dev"))
    assert len(rules) == 1
    assert rules[0]["FromPort"] == rules[0]["ToPort"] == 8501
    assert "SourcePrefixListId" in rules[0] and "CidrIp" not in rules[0]   # no 0.0.0.0/0, no SSH


def test_server_is_graviton_with_imdsv2():
    t = template("dev")
    t.has_resource_properties("AWS::EC2::Instance", {"InstanceType": "t4g.small"})
    t.has_resource_properties("AWS::EC2::LaunchTemplate", {
        "LaunchTemplateData": {"MetadataOptions": {"HttpTokens": "required"}}})


def test_no_nat_gateway():
    template("dev").resource_count_is("AWS::EC2::NatGateway", 0)


def test_data_disk_is_encrypted_and_survives_the_stack():
    template("dev").has_resource("AWS::EC2::Volume", {
        "Properties": {"Encrypted": True}, "DeletionPolicy": "Snapshot"})
    template("prod").has_resource("AWS::EC2::Volume", {"DeletionPolicy": "Retain"})


def test_cloudfront_forces_https_and_caches_nothing():
    template("dev").has_resource_properties("AWS::CloudFront::Distribution", {
        "DistributionConfig": {"DefaultCacheBehavior": {
            "ViewerProtocolPolicy": "redirect-to-https",
            "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",   # AWS managed CachingDisabled
        }}})
