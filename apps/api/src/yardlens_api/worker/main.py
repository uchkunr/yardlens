import asyncio
import contextlib
import signal
import sys

from pydantic import ValidationError

from yardlens_api.core.logging import configure_logging
from yardlens_api.worker.agent import Agent
from yardlens_api.worker.config import WorkerSettings


def main() -> None:
    """Run a worker (``yardlens-worker``): it connects to the server and processes the cameras
    the server gives it. Configured by environment variables; see ``WorkerSettings``."""
    try:
        settings = WorkerSettings()  # pyright: ignore[reportCallIssue] - read from the environment
    except ValidationError as exc:
        missing = ", ".join(f"YARDLENS_{str(error['loc'][0]).upper()}" for error in exc.errors())
        sys.exit(f"yardlens-worker: set {missing} (copy the command from Settings > Workers)")
    configure_logging(settings.log_level, json_output=settings.log_json)
    agent = Agent(settings)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            # Windows has no signal handlers in asyncio; Ctrl+C still ends the process.
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(stop_signal, agent.stop)
        await agent.run()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())
