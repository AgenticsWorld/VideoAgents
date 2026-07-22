import os

import uvicorn

DEFAULT_HOST = "127.0.0.1"


def main() -> None:
    host = os.environ.get("VIDEOAGENTS_HOST", DEFAULT_HOST)
    port = int(os.environ.get("VIDEOAGENTS_PORT", "8630"))
    uvicorn.run("services.api.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
