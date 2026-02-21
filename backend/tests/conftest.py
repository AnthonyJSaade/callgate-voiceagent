import sys


def pytest_sessionstart(session) -> None:  # noqa: ARG001
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")
