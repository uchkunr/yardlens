"""Worker tokens, the CA fingerprint workers pin, and the commands that start a worker."""

import base64
import binascii
import hashlib
import shlex
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from yardlens_api.core.security import new_token, same, token_hash
from yardlens_api.db.models import Worker
from yardlens_api.schemas.workers import WorkerCommands

TOKEN_PREFIX = "ylw"  # noqa: S105 - a prefix, not a secret


def issue_token(worker_id: int) -> tuple[str, str]:
    """A new token for the worker, and the hash to store. The token is shown once."""
    secret = new_token()
    return f"{TOKEN_PREFIX}_{worker_id}_{secret}", token_hash(secret)


def parse_token(token: str) -> tuple[int, str] | None:
    """``ylw_<id>_<secret>`` into the worker id and the secret."""
    prefix, _, rest = token.partition("_")
    worker_id, _, secret = rest.partition("_")
    if prefix != TOKEN_PREFIX or not (worker_id.isascii() and worker_id.isdigit()) or not secret:
        return None
    return int(worker_id), secret


async def authenticate(session: AsyncSession, authorization: str | None) -> Worker | None:
    """The worker an ``Authorization: Bearer`` header belongs to, if any."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    parsed = parse_token(authorization.removeprefix("Bearer ").strip())
    if parsed is None:
        return None
    worker_id, secret = parsed
    worker = await session.get(Worker, worker_id)
    # Hashed either way, so an unknown id takes as long as a wrong secret.
    digest = token_hash(secret)
    if worker is None or not same(digest, worker.token_hash):
        return None
    return worker


def certificate_fingerprint(pem: bytes) -> str:
    """SHA-256 of the first certificate in a PEM file: what browsers show as its fingerprint."""
    lines = pem.decode("ascii", errors="ignore").splitlines()
    try:
        start = lines.index("-----BEGIN CERTIFICATE-----")
        end = lines.index("-----END CERTIFICATE-----", start)
        der = base64.b64decode("".join(lines[start + 1 : end]), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Not a PEM certificate") from exc
    return hashlib.sha256(der).hexdigest()


def ca_pin(path: Path | None) -> str | None:
    """The fingerprint of the certificate authority HTTPS uses here, when there is one."""
    if path is None or not path.is_file():
        return None
    try:
        return certificate_fingerprint(path.read_bytes())
    except ValueError, OSError:
        return None


def worker_commands(
    *, server: str, token: str, image: str, version: str, pin: str | None
) -> WorkerCommands:
    """``docker run`` lines that start the worker; one for an NVIDIA GPU."""
    env = {"YARDLENS_SERVER": server, "YARDLENS_WORKER_TOKEN": token}
    if pin:
        env["YARDLENS_CA_PIN"] = pin
    options = " ".join(f"-e {name}={shlex.quote(value)}" for name, value in env.items())
    base = "docker run -d --name yardlens-worker --restart unless-stopped -v yardlens-worker:/data"
    return WorkerCommands(
        cpu=f"{base} {options} {image}:{version}-cpu",
        gpu=f"{base} --gpus all {options} {image}:{version}-cuda",
    )
