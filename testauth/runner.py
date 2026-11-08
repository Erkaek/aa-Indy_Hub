"""Run offline tests with a private Redis and explicit disposable DB settings."""

# Standard Library
import errno
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def execute(labels):
    """Block external traffic before Django can initialize any app."""
    os.environ["DJANGO_SETTINGS_MODULE"] = "testauth.settings.local"
    with tempfile.TemporaryDirectory(prefix="indyhub-tests-") as directory:
        redis_socket = str(Path(directory) / "redis.sock")
        os.environ["INDY_HUB_TEST_REDIS_URL"] = f"unix://{redis_socket}?db=1"
        original_connect = socket.socket.connect
        database_host = os.environ.get("INDY_HUB_TEST_DB_HOST", "")
        database_port = int(os.environ.get("INDY_HUB_TEST_DB_PORT") or 3306)
        if database_host and database_host not in {"127.0.0.1", "localhost", "::1"}:
            if not database_host.startswith("/"):
                raise ValueError("Tests require a loopback database or Unix socket")

        def isolated_connect(sock, address):
            if sock.family == socket.AF_UNIX and address in {
                redis_socket,
                database_host,
            }:
                return original_connect(sock, address)
            if (
                database_host in {"127.0.0.1", "localhost", "::1"}
                and isinstance(address, tuple)
                and address[0] in {"127.0.0.1", "::1"}
                and address[1] == database_port
            ):
                return original_connect(sock, address)
            raise OSError(
                errno.ENETUNREACH, "External connection blocked by test runner"
            )

        with (Path(directory) / "redis.log").open("w") as log:
            redis = subprocess.Popen(
                [
                    "redis-server",
                    "--port",
                    "0",
                    "--unixsocket",
                    redis_socket,
                    "--unixsocketperm",
                    "700",
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                ],
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    if Path(redis_socket).exists():
                        break
                    if redis.poll() is not None:
                        raise RuntimeError("Private test Redis failed to start")
                    time.sleep(0.02)
                if not Path(redis_socket).exists():
                    raise RuntimeError("Private test Redis did not become ready")
                with patch.object(socket.socket, "connect", isolated_connect):
                    # Django
                    from django.core.management import execute_from_command_line

                    execute_from_command_line(
                        ["runtests.py", "test", "--noinput", *labels]
                    )
            finally:
                redis.terminate()
                redis.wait(timeout=10)
