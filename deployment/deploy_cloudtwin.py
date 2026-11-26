import os
import sys
import time

import boto3


region = os.environ["AWS_DEFAULT_REGION"]
instance_id = os.environ["CLOUDTWIN_INSTANCE_ID"]
image_uri = os.environ["CLOUDTWIN_IMAGE_URI"]
collector_role_arn = os.environ["CLOUDTWIN_COLLECTOR_ROLE_ARN"]

ssm = boto3.client("ssm", region_name=region)

commands = [
    "set -e",

    'echo "=== CLOUDTWIN DEPLOYMENT ==="',

    f'IMAGE_URI="{image_uri}"',
    f'REGION="{region}"',

    'REGISTRY="$(echo "$IMAGE_URI" | cut -d/ -f1)"',

    'aws ecr get-login-password --region "$REGION" | '
    'sudo docker login --username AWS --password-stdin "$REGISTRY"',

    'sudo docker pull "$IMAGE_URI"',

    'sudo docker rm -f cloudtwin-previous 2>/dev/null || true',

    'if sudo docker inspect cloudtwin >/dev/null 2>&1; then '
    'sudo docker stop cloudtwin && '
    'sudo docker rename cloudtwin cloudtwin-previous; fi',

    'sudo docker run -d '
    '--name cloudtwin '
    '--network host '
    '--restart unless-stopped '
    f'-e AWS_REGION="{region}" '
    f'-e AWS_DEFAULT_REGION="{region}" '
    f'-e CLOUDTWIN_COLLECTOR_ROLE_ARN="{collector_role_arn}" '
    '"$IMAGE_URI"',

    'HEALTHY=0',

    'for i in $(seq 1 30); do '
    'if curl -fsS http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; '
    'then HEALTHY=1; break; fi; sleep 2; done',

    'if [ "$HEALTHY" != "1" ]; then '
    'echo "New version unhealthy - rollback"; '
    'sudo docker logs --tail 100 cloudtwin || true; '
    'sudo docker rm -f cloudtwin || true; '
    'if sudo docker inspect cloudtwin-previous >/dev/null 2>&1; then '
    'sudo docker rename cloudtwin-previous cloudtwin; '
    'sudo docker start cloudtwin; fi; '
    'exit 1; fi',

    'echo "=== DEPLOYMENT HEALTHY ==="',
    'curl -fsS http://127.0.0.1:8501/_stcore/health',
    'echo',
    'sudo docker inspect cloudtwin --format "{{.Config.Image}}"',
]

response = ssm.send_command(
    InstanceIds=[instance_id],
    DocumentName="AWS-RunShellScript",
    Parameters={"commands": commands},
)

command_id = response["Command"]["CommandId"]

print(f"SSM command: {command_id}")

for _ in range(120):
    time.sleep(2)

    try:
        result = ssm.get_command_invocation(
            CommandId=command_id,
            InstanceId=instance_id,
        )
    except ssm.exceptions.InvocationDoesNotExist:
        continue

    status = result["Status"]

    if status in {
        "Success",
        "Failed",
        "Cancelled",
        "TimedOut",
        "Cancelling",
    }:
        print(result.get("StandardOutputContent", ""))

        if result.get("StandardErrorContent"):
            print(
                result["StandardErrorContent"],
                file=sys.stderr,
            )

        if status != "Success":
            raise SystemExit(
                f"Deployment failed with SSM status: {status}"
            )

        print("CloudTwin deployment completed successfully.")
        raise SystemExit(0)

raise SystemExit("Timed out waiting for EC2 deployment.")
