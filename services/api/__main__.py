import os

import uvicorn

from . import app as api_app

DEFAULT_HOST = "127.0.0.1"


class _ApiServer(uvicorn.Server):
    """Wake SSE streams as soon as a shutdown signal arrives."""

    def handle_exit(self, sig, frame) -> None:
        api_app.request_shutdown()
        super().handle_exit(sig, frame)


def main() -> None:
    host = os.environ.get("VIDEOAGENTS_HOST", DEFAULT_HOST)
    port = int(os.environ.get("VIDEOAGENTS_PORT", "8630"))
    config = uvicorn.Config(
        api_app.app, host=host, port=port, log_level="info",
        timeout_graceful_shutdown=5,
    )
    try:
        _ApiServer(config).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
