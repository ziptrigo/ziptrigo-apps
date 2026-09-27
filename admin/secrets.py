#!python
"""
Backup/restore `.env` and other files using ``fsspec``.
"""

import difflib
from functools import cache
from pathlib import Path
from posixpath import join as pjoin
from tempfile import TemporaryDirectory
from typing import Annotated

import fsspec
import pyzipper
import typer
from rich.console import Console
from rich.syntax import Syntax

from admin import ADMIN_DIR, APP_NAME, PROJECT_ROOT
from admin.utils import logger, read_env_file_from_path

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

TARGET = f's3://files-058264312820/{APP_NAME}/'

TargetAnnotation = Annotated[
    str,
    typer.Argument(
        help='Target filespec URI (e.g. `s3://bucket/envs/` or `/local/backup/`).',
    ),
]


def get_secrets_files() -> list[str]:
    """Return list of secret files from ``secrets_files.txt``. Paths are not resolved."""
    secrets_files = ADMIN_DIR / 'secrets_files.txt'
    with open(secrets_files) as f:
        return [
            stripped for line in f if not (stripped := line.strip()).startswith('#') and stripped
        ]


@cache
def get_secrets_password() -> str:
    return read_env_file_from_path(ADMIN_DIR / '.env')['SECRETS_PASSWORD']


def _zip_filename(env_file: Path) -> str:
    """Return a unique zip filename for *env_file* that preserves subfolder structure."""
    return env_file.as_posix().replace('/', '_') + '.zip'


def encrypt_zip(source_file: Path, zip_name: Path, arcname: str | None = None):
    pwd = get_secrets_password()
    with pyzipper.AESZipFile(
        zip_name, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(pwd.encode())
        zf.write(source_file, arcname=arcname or source_file.name)


def decrypt_zip(zip_name: Path, target_dir: Path | None = None):
    pwd = get_secrets_password()
    with pyzipper.AESZipFile(zip_name, 'r') as zf:
        zf.setpassword(pwd.encode())
        zf.extractall(target_dir)


def _get_fs_and_base_path(target: str) -> tuple[fsspec.AbstractFileSystem, str]:
    fs, _, paths = fsspec.get_fs_token_paths(target)
    return fs, paths[0].rstrip('/')


def _get_remote_path(base_path: str, env_file: Path) -> str:
    return pjoin(base_path, f'{env_file.as_posix()}.zip')


def _download_encrypted_env(
    fs: fsspec.AbstractFileSystem, remote_path: str, local_path: Path
) -> bool:
    try:
        fs.get_file(remote_path, str(local_path))
    except FileNotFoundError:
        return False
    return True


def _get_remote_file(
    fs: fsspec.AbstractFileSystem,
    remote_path: str,
    env_file: Path,
    temp_dir: Path,
) -> Path | None:
    encrypted_path = temp_dir / _zip_filename(env_file)
    extracted_dir = temp_dir / env_file.parent
    extracted_dir.mkdir(parents=True, exist_ok=True)

    if not _download_encrypted_env(fs, remote_path, encrypted_path):
        return None

    decrypt_zip(encrypted_path, extracted_dir)
    return extracted_dir / env_file.name


@app.command(name='diff')
def secrets_diff(target: TargetAnnotation = TARGET):
    """Show a diff of local `.env` files with the ones at *target*."""
    console = Console()
    fs, base_path = _get_fs_and_base_path(target)

    with TemporaryDirectory() as temp:
        temp_dir = Path(temp)

        for rel_path in get_secrets_files():
            local_path = PROJECT_ROOT / rel_path
            if not local_path.exists():
                logger.warning(f'Local file not found: {rel_path}')
                continue

            env_file = Path(rel_path)
            remote_path = _get_remote_path(base_path, env_file)
            remote_file = _get_remote_file(fs, remote_path, env_file, temp_dir)

            if remote_file is None:
                console.print(f'[yellow]{rel_path}[/yellow]: missing at target')
                continue

            with open(local_path) as f1, open(remote_file) as f2:
                diff_lines = list(
                    difflib.unified_diff(
                        f1.readlines(),
                        f2.readlines(),
                        fromfile=str(local_path),
                        tofile=remote_path,
                    )
                )

            if not diff_lines:
                console.print(f'Identical: [b]{rel_path}[/b]')
                continue

            diff_text = ''.join(diff_lines)
            console.print(Syntax(diff_text, 'diff', theme='monokai', line_numbers=True))


@app.command(name='backup')
def secrets_backup(target: TargetAnnotation = TARGET):
    """Back up secrets files to *target*."""
    fs, base_path = _get_fs_and_base_path(target)

    with TemporaryDirectory() as temp:
        temp_dir = Path(temp)

        for rel_path in get_secrets_files():
            local_path = PROJECT_ROOT / rel_path
            if not local_path.exists():
                logger.warning(f'Local file not found: {rel_path}')
                continue

            env_file = Path(rel_path)
            encrypted_path = temp_dir / _zip_filename(env_file)
            encrypt_zip(local_path, encrypted_path)
            remote_path = _get_remote_path(base_path, env_file)
            fs.makedirs(fs._parent(remote_path), exist_ok=True)
            fs.put_file(str(encrypted_path), remote_path)
            logger.info(f'Uploaded {rel_path} to {remote_path}')


@app.command(name='restore')
def secrets_restore(target: TargetAnnotation = TARGET):
    """Restore secrets files from *target*."""
    fs, base_path = _get_fs_and_base_path(target)

    with TemporaryDirectory() as temp:
        temp_dir = Path(temp)

        for rel_path in get_secrets_files():
            local_path = PROJECT_ROOT / rel_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            env_file = Path(rel_path)
            remote_path = _get_remote_path(base_path, env_file)
            encrypted_path = temp_dir / _zip_filename(env_file)

            if not _download_encrypted_env(fs, remote_path, encrypted_path):
                logger.warning(f'Remote file not found: {remote_path}')
                continue

            decrypt_zip(encrypted_path, local_path.parent)
            logger.info(f'Downloaded {remote_path} to {rel_path}')


if __name__ == '__main__':
    app()
