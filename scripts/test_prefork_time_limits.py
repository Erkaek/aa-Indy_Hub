"""Exercise real prefork timeouts with a private broker and synthetic tasks."""

# Standard Library
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="indyhub-prefork-") as directory:
        redis_socket = str(Path(directory) / "redis.sock")
        os.environ["INDY_HUB_PROBE_REDIS_SOCKET"] = redis_socket
        with (Path(directory) / "redis.log").open("w") as redis_log:
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
                stdout=redis_log,
                stderr=redis_log,
            )
            try:
                for _ in range(100):
                    if Path(redis_socket).exists():
                        break
                    time.sleep(0.02)
                if not Path(redis_socket).exists():
                    raise RuntimeError("Private Redis failed to start")
                qualify(directory)
            finally:
                redis.terminate()
                redis.wait(timeout=10)


def qualify(directory):
    # Third Party
    from billiard.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

    # Local
    from prefork_probe_app import app

    routes = {
        name: app.amqp.router.route({}, name)["queue"].name
        for name in ["indy_hub.tasks._probe.success", "discord.update", "other.task"]
    }
    assert list(routes.values()) == ["indy_hub", "services", "celery"], routes
    log_path = Path(directory) / "worker.log"
    child_env = os.environ.copy()
    child_env.pop("DJANGO_SETTINGS_MODULE", None)
    with log_path.open("w") as worker_log:
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "prefork_probe_app",
                "worker",
                "--pool=prefork",
                "--concurrency=1",
                "-Q",
                "indy_hub",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                "--loglevel=INFO",
            ],
            cwd=Path(__file__).parent,
            env=child_env,
            stdout=worker_log,
            stderr=worker_log,
        )
        try:
            assert (
                app.send_task("indy_hub.tasks._probe.success").get(timeout=25) == "ok"
            )
            failures = []
            for name, expected in [
                ("soft", SoftTimeLimitExceeded),
                ("hard", TimeLimitExceeded),
            ]:
                try:
                    app.send_task("indy_hub.tasks._probe." + name).get(timeout=15)
                except expected:
                    failures.append(expected.__name__)
                else:
                    raise AssertionError(f"{name} time limit was not enforced")
            assert (
                app.send_task("indy_hub.tasks._probe.success").get(timeout=15) == "ok"
            )
            print(
                json.dumps(
                    {"routes": routes, "timeouts": failures, "recovery": "PASS"},
                    indent=2,
                )
            )
        except BaseException:
            print(log_path.read_text(), file=sys.stderr)
            raise
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)


if __name__ == "__main__":
    main()
