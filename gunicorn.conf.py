"""Gunicorn hooks. The CLI flags live in `supervise.py` / the Dockerfile; this only adds hooks.

`post_worker_init` runs after the worker has loaded `config.wsgi`, so Django is configured and the
scheduler registry (filled from each app's `AppConfig.ready()`) is populated. Deliberately no
`preload_app`: each worker starts its own scheduler thread after the fork.
"""

_runner = None


def post_worker_init(worker):
    global _runner
    # Imported here, not at module level: gunicorn loads this file in the master before Django
    # settings exist, and `apps.core.scheduler` imports models.
    from apps.core.scheduler import start_scheduler_thread

    _runner = start_scheduler_thread()


def worker_exit(server, worker):
    if _runner is not None:
        _runner.stop()
