"""Hosting for one environment: image registry, one EC2 server, its data disk, secrets, CloudFront.

    browser --https--> CloudFront --http:8501 (CloudFront IPs only)--> EC2 [compose: ui -> api]
                                                                         |
                                                       /srv/setuhaul (own EBS disk, daily snapshots)
"""

import json

from aws_cdk import CfnOutput, CfnTag, Duration, RemovalPolicy, Size, Stack, Tags
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_dlm as dlm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

UI_PORT = 8501
APP_UID = 10001                      # the non-root user inside our image
COMPOSE_VERSION = "v5.3.1"
COMPOSE_SHA256 = "aa611e811d0ea25897839c404bfb5bf93ce706dc51c500a4457890f5d0606a86"


class HostingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        prod = env_name == "prod"

        # ---- images ----
        self.repo = ecr.Repository(
            self, "Images",
            repository_name=f"setuhaul-{env_name}",
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,   # a tag (git SHA) always means the same image
            image_scan_on_push=True,                            # CVE scan of OS + Python packages on every push
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=10, description="keep the last 10 images")],
            removal_policy=RemovalPolicy.RETAIN if prod else RemovalPolicy.DESTROY,
            empty_on_delete=not prod,                           # dev/staging: delete images with the stack
        )

        # ---- secrets: generated session secret; LLM key is set once by hand (never in git) ----
        self.secret = secretsmanager.Secret(
            self, "AppSecrets",
            secret_name=f"setuhaul/{env_name}/app",
            description="SetuHaul runtime secrets: LLM_API_KEY, SESSION_SECRET",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({"LLM_API_KEY": "set-me"}),
                generate_string_key="SESSION_SECRET",
                password_length=48,
                exclude_punctuation=True,
            ),
            removal_policy=RemovalPolicy.RETAIN if prod else RemovalPolicy.DESTROY,
        )

        # ---- network: our own small VPC, one public subnet, no NAT gateway (~$40/month saved) ----
        vpc = ec2.Vpc(
            self, "Net",
            max_azs=1,
            nat_gateways=0,
            subnet_configuration=[ec2.SubnetConfiguration(
                name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24)],
        )
        subnet = vpc.public_subnets[0]

        # ---- firewall: the UI port is open to CloudFront's servers only. No SSH, no API port ----
        cloudfront_ips = ec2.PrefixList.from_lookup(
            self, "CloudFrontOrigins", prefix_list_name="com.amazonaws.global.cloudfront.origin-facing")
        server_sg = ec2.SecurityGroup(
            self, "ServerSg", vpc=vpc, allow_all_outbound=True,
            description="SetuHaul server: Streamlit UI from CloudFront only")
        server_sg.add_ingress_rule(
            ec2.Peer.prefix_list(cloudfront_ips.prefix_list_id), ec2.Port.tcp(UI_PORT), "UI, CloudFront only")

        # ---- data disk: outlives the server; the server attaches it itself at boot ----
        data_volume = ec2.Volume(
            self, "Data",
            availability_zone=subnet.availability_zone,
            size=Size.gibibytes(10),
            volume_type=ec2.EbsDeviceVolumeType.GP3,
            encrypted=True,
            removal_policy=RemovalPolicy.RETAIN if prod else RemovalPolicy.SNAPSHOT,
        )
        Tags.of(data_volume).add("setuhaul-backup", env_name)

        # ---- server identity: Session Manager, pull images, read secrets, attach its data disk ----
        role = iam.Role(
            self, "ServerRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")],
        )
        self.repo.grant_pull(role)
        self.secret.grant_read(role)
        role.add_to_policy(iam.PolicyStatement(actions=["ec2:DescribeVolumes"], resources=["*"]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["ec2:AttachVolume"],
            resources=[
                f"arn:aws:ec2:{self.region}:{self.account}:volume/{data_volume.volume_id}",
                f"arn:aws:ec2:{self.region}:{self.account}:instance/*",
            ],
            conditions={"StringEquals": {"aws:ResourceTag/aws:cloudformation:stack-name": self.stack_name}},
        ))
        # The volume ARN has no CloudFormation tag, so allow it unconditionally in a second statement.
        role.add_to_policy(iam.PolicyStatement(
            actions=["ec2:AttachVolume"],
            resources=[f"arn:aws:ec2:{self.region}:{self.account}:volume/{data_volume.volume_id}"],
        ))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euo pipefail",
            # Docker + a pinned, checksum-verified Compose plugin
            "dnf install -y docker",
            "systemctl enable --now docker",
            "mkdir -p /usr/local/lib/docker/cli-plugins",
            f"curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose "
            f"https://github.com/docker/compose/releases/download/{COMPOSE_VERSION}/docker-compose-linux-aarch64",
            f"echo '{COMPOSE_SHA256}  /usr/local/lib/docker/cli-plugins/docker-compose' | sha256sum -c -",
            "chmod +x /usr/local/lib/docker/cli-plugins/docker-compose",
            # Attach the data disk; after a server replacement, wait until the old server releases it
            f"VOL={data_volume.volume_id}",
            f"REGION={self.region}",
            'IMDS=$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")',
            'IID=$(curl -s -H "X-aws-ec2-metadata-token: $IMDS" http://169.254.169.254/latest/meta-data/instance-id)',
            "for i in $(seq 1 90); do",
            '  STATE=$(aws ec2 describe-volumes --region $REGION --volume-ids $VOL --query "Volumes[0].State" --output text)',
            '  if [ "$STATE" = available ]; then aws ec2 attach-volume --region $REGION --volume-id $VOL --instance-id $IID --device /dev/sdf && break; fi',
            "  sleep 10",
            "done",
            'DEV=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${VOL/-/}',
            'for i in $(seq 1 60); do [ -e "$DEV" ] && break; sleep 2; done',
            'blkid "$DEV" || mkfs.ext4 -L setuhaul-data "$DEV"',          # format only if brand new
            "mkdir -p /srv/setuhaul",
            "grep -q setuhaul-data /etc/fstab || echo 'LABEL=setuhaul-data /srv/setuhaul ext4 defaults,nofail 0 2' >> /etc/fstab",
            "mount -a",
            "mkdir -p /srv/setuhaul/data",
            f"chown {APP_UID}:{APP_UID} /srv/setuhaul/data",
        )

        self.server = ec2.Instance(
            self, "Server",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=[subnet]),
            instance_type=ec2.InstanceType("t4g.small"),        # Graviton (arm64), matches our image
            machine_image=ec2.MachineImage.latest_amazon_linux2023(cpu_type=ec2.AmazonLinuxCpuType.ARM_64),
            security_group=server_sg,
            role=role,
            associate_public_ip_address=True,                   # outbound internet without a NAT gateway
            require_imdsv2=True,                                # blocks SSRF-style theft of the role's credentials
            block_devices=[ec2.BlockDevice(
                device_name="/dev/xvda",
                volume=ec2.BlockDeviceVolume.ebs(20, volume_type=ec2.EbsDeviceVolumeType.GP3, encrypted=True),
            )],
            user_data=user_data,
            user_data_causes_replacement=True,                  # the server is disposable; data lives on its own disk
        )

        # ---- daily snapshots of the data disk, keep 7 ----
        dlm_role = iam.Role(
            self, "BackupRole",
            assumed_by=iam.ServicePrincipal("dlm.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSDataLifecycleManagerServiceRole")],
        )
        dlm.CfnLifecyclePolicy(
            self, "DataBackups",
            description=f"SetuHaul {env_name} data disk - daily - keep 7",   # DLM allows only [A-Za-z0-9 _-]
            execution_role_arn=dlm_role.role_arn,
            state="ENABLED",
            policy_details=dlm.CfnLifecyclePolicy.PolicyDetailsProperty(
                resource_types=["VOLUME"],
                target_tags=[CfnTag(key="setuhaul-backup", value=env_name)],
                schedules=[dlm.CfnLifecyclePolicy.ScheduleProperty(
                    name="daily",
                    create_rule=dlm.CfnLifecyclePolicy.CreateRuleProperty(
                        interval=24, interval_unit="HOURS", times=["17:00"]),   # 17:00 UTC = 22:30 IST
                    retain_rule=dlm.CfnLifecyclePolicy.RetainRuleProperty(count=7),
                    copy_tags=True,
                )],
            ),
        )

        # ---- CloudFront: HTTPS for free (no domain needed), WebSockets pass through ----
        self.cdn = cloudfront.Distribution(
            self, "Cdn",
            comment=f"SetuHaul {env_name}",
            price_class=cloudfront.PriceClass.PRICE_CLASS_200,  # includes India + Australia edges
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.HttpOrigin(
                    self.server.instance_public_dns_name,
                    http_port=UI_PORT,
                    protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
                    read_timeout=Duration.seconds(60),
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,          # a live app, nothing to cache
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,  # Streamlit needs Host + WS headers
            ),
        )

        CfnOutput(self, "ImageRepoUri", value=self.repo.repository_uri)
        CfnOutput(self, "ServerId", value=self.server.instance_id)
        CfnOutput(self, "SecretArn", value=self.secret.secret_arn)
        CfnOutput(self, "DataVolumeId", value=data_volume.volume_id)
        CfnOutput(self, "AppUrl", value=f"https://{self.cdn.distribution_domain_name}")
