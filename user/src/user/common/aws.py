import os

import boto3
from botocore.client import BaseClient


def get_aws_params():
    """Get AWS credentials from environment variables."""
    access_key = os.getenv('AWS_ACCESS_KEY_ID')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    role = os.getenv('AWS_ROLE')
    region = os.getenv('AWS_REGION')

    if not access_key and secret_key and role and region:
        raise RuntimeError('AWS credentials not found in environment variables.')

    # Order is the same as in `boto3_client(...)`
    return access_key, secret_key, role, region


def boto3_client(
    service: str,
    access_key: str | None,
    secret_key: str | None,
    role: str | None,
    region: str | None = None,
    session_name: str = 'S3Session',
) -> BaseClient:
    """
    Create an S3 client by assuming a role.

    :param service: Service name, e.g. ``s3`` or ``ses``.
    :param access_key: IAM user access key ID.
    :param secret_key: IAM user secret access key.
    :param role: ARN of the role to assume.
    :param region: AWS region.
    :param session_name: Name for the assumed role session.

    :returns: ``boto3`` client with assumed role credentials.
    """
    # Create STS client with user credentials
    sts_client = boto3.client(
        'sts', aws_access_key_id=access_key, aws_secret_access_key=secret_key, region_name=region
    )

    # Assume the role
    response = sts_client.assume_role(RoleArn=role, RoleSessionName=session_name)

    # Extract temporary credentials
    credentials = response['Credentials']

    return boto3.client(  # type: ignore
        service,
        aws_access_key_id=credentials['AccessKeyId'],
        aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken'],
    )
