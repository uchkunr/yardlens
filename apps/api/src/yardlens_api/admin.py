"""Create the administrator or reset the password, from the machine YardLens runs on.

    uv run yardlens-admin create          # asks for a username and a password
    uv run yardlens-admin set-password    # asks for a new password; signs out every device

With ``--password-stdin`` the password is read from standard input instead (scripts, Docker).
There is deliberately no way to do either from the dashboard without being signed in.
"""

import argparse
import asyncio
import getpass
import sys
from datetime import timedelta

from yardlens_api.core.config import get_settings
from yardlens_api.core.errors import AppError
from yardlens_api.core.security import load_secret, password_hasher
from yardlens_api.db.session import create_engine, create_session_factory, init_models
from yardlens_api.services.auth import AuthService


def _password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    if getpass.getpass("Repeat: ") != first:
        raise SystemExit("The passwords do not match")
    return first


async def _run(command: str, username: str, password: str) -> None:
    config = get_settings()
    engine = create_engine(config.database_url)
    try:
        await init_models(engine)
        auth = AuthService(
            session_factory=create_session_factory(engine),
            secret=load_secret(config.auth_secret, config.data_dir / "auth-secret"),
            hasher=password_hasher(),
            access_ttl=timedelta(minutes=config.access_token_minutes),
            refresh_ttl=timedelta(days=config.refresh_token_days),
        )
        if command == "create":
            await auth.create_admin(username, password)
        else:
            await auth.set_password(username, password)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the YardLens administrator.")
    parser.add_argument("command", choices=["create", "set-password"])
    parser.add_argument("--username", help="asked for when left out")
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args()
    username = args.username or input("Username: ").strip()
    password = _password(args.password_stdin)
    try:
        asyncio.run(_run(args.command, username, password))
    except AppError as exc:
        raise SystemExit(exc.detail) from exc
    print("Done." if args.command == "create" else "Password changed; every device signed out.")


if __name__ == "__main__":
    main()
