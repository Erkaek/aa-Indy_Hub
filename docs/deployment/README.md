# Celery worker setup

## Worker configuration

Indy Hub's task time limits require a prefork worker. Keep the other applications'
workers as configured and add an Indy Hub consumer with its own queue.

In the host Celery configuration, merge this route with the existing routes:

```python
app.conf.task_routes = {
    # Keep every existing route, including any services/Discord routes.
    "indy_hub.tasks.*": {"queue": "indy_hub"},
}
```

Add the [Supervisor worker block](indy_hub-worker.conf) to your Supervisor
configuration and include `worker_indy_hub` in the application's process group.
The example uses the same `AA_*` environment variables as the host configuration;
adapt those variables and paths to your installation. Its shutdown wait is
960 seconds, longer than the existing 900-second task limit.

Exclude `indy_hub` from general threads workers with `-X indy_hub`. Workers with
explicit `-Q` selections must leave that queue to the prefork consumer. Keep
services/Discord workers on their existing queue and pool.

For Docker or another process manager, apply the same task route and run a
dedicated consumer with `--pool=prefork -Q indy_hub` using your stack's Celery
application name and environment. The Supervisor file is an example for
Supervisor installations, not a Docker command.

Reload the process-manager configuration and restart the affected publishers,
workers and Beat through your normal deployment procedure. Avoid duplicate
consumers or discarded messages. Tasks already published to an old queue keep
their original routing; review and drain that backlog during a maintenance
window. A new route does not move previously queued tasks.
