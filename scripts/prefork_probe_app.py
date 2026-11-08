"""Synthetic tasks for the isolated prefork qualification, with no Django app."""

# Standard Library
import errno
import os
import signal
import socket
import time

# Third Party
from celery import Celery

_socket_path = os.environ["INDY_HUB_PROBE_REDIS_SOCKET"]
_connect = socket.socket.connect


def _private_connect(sock, address):
    if sock.family == socket.AF_UNIX and address == _socket_path:
        return _connect(sock, address)
    raise OSError(errno.ENETUNREACH, "Worker probe permits only its private Redis")


socket.socket.connect = _private_connect
app = Celery("indyhub_prefork_probe", fixups=[])
app.conf.update(
    broker_url=f"redis+socket://{_socket_path}?virtual_host=0",
    result_backend=f"redis+socket://{_socket_path}?virtual_host=1",
    task_routes={
        "discord.*": {"queue": "services"},
        "indy_hub.tasks.*": {"queue": "indy_hub"},
    },
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    broker_connection_retry_on_startup=False,
)


@app.task(name="indy_hub.tasks._probe.success")
def success():
    return "ok"


@app.task(name="indy_hub.tasks._probe.soft", soft_time_limit=1, time_limit=4)
def soft():
    time.sleep(6)


@app.task(name="indy_hub.tasks._probe.hard", soft_time_limit=1, time_limit=2)
def hard():
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    time.sleep(6)
