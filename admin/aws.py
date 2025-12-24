#!python
"""
AWS login.

Requires the AWS CLI to be installed.

https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-welcome.html
"""
import os
from pathlib import Path
from typing import Annotated

import typer

from admin.utils import DryAnnotation, logger, run

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)


def select_aws_profile(profile: str | None = None) -> str:
    """
    Returns the AWS profile. Allows for multiple profiles.

    1. Check ``.env`` for the selected profile.
    2. Check profiles in ``~/.aws/config``.
       2.1 If only one profile is in the config file, use that.
       2.2 If multiple profiles are in the config file, ask the user.

    Note that in the AWS config file, the section is called ``profile AWSGeneral-399484477925``
    and the profile is ``AWSGeneral-399484477925``.
    """
    import configparser

    config_file = Path('~/.aws/config').expanduser()
    _config = configparser.ConfigParser()
    _config.read(config_file)
    profile_sections = [
        section for section in _config.sections() if section.lower().startswith('profile ')
    ]
    aws_profiles = [profile.removeprefix('profile ') for profile in profile_sections]
    aws_profile = profile or os.getenv('AWS_PROFILE')
    if not aws_profile or (aws_profile not in aws_profiles):
        if len(aws_profiles) == 1:
            aws_profile = aws_profiles[0]
        else:
            from rich.prompt import Prompt

            console_input = Prompt.ask(
                'Select AWS Profile',
                choices=aws_profiles,
            )
            aws_profile = console_input
            if aws_profile is None:
                logger.error('No AWS profile selected.')
                raise typer.Exit(code=1)

            logger.info(
                'A default profile can be added in `config_utils.json` under `aws_profile` key, '
                'or in the environment variable `AWS_PROFILE`.'
            )

    if not aws_profile:
        logger.error(f'AWS profile not found in `{config_file}`')
        raise typer.Exit(1)
    return aws_profile


@app.command(name='login')
def aws_login(
    profile: Annotated[
        str | None, typer.Option(help='AWS profile to use. ', show_default=False)
    ] = None,
    dry: DryAnnotation = False,
):
    """
    Login to AWS, to use `aws` in CLI and `boto3` in Python.
    """
    run('aws', 'sso', 'login', '--profile', select_aws_profile(profile), dry=dry)


if __name__ == '__main__':
    app()
