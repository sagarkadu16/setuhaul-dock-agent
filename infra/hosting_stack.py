"""Hosting for one environment. 14e-1: the image registry. The server (14e-2) and CloudFront (14e-3) come next."""

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from constructs import Construct


class HostingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        prod = env_name == "prod"

        self.repo = ecr.Repository(
            self, "Images",
            repository_name=f"setuhaul-{env_name}",
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,   # a tag (git SHA) always means the same image
            image_scan_on_push=True,                            # CVE scan of OS + Python packages on every push
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=10, description="keep the last 10 images")],
            removal_policy=RemovalPolicy.RETAIN if prod else RemovalPolicy.DESTROY,
            empty_on_delete=not prod,                           # dev/staging: delete images with the stack
        )

        CfnOutput(self, "ImageRepoUri", value=self.repo.repository_uri)