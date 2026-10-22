# python -m app.worker.run  (started by the worker service)

import threading

from rq import Worker

from app.config import settings
from app.logging_config import configure_logging, get_logger
from app.worker.queue import QUEUE_NAME, get_queue, get_redis

log = get_logger(__name__)

AUTO_SYNC_TASK = "app.worker.tasks.auto_sync_applications_task"
AUTO_SYNC_LOCK = "auto-sync:applications"


def queue_auto_sync(interval_seconds: int) -> bool:
    # the lock lives for one interval, so several workers or a restart queue it once
    if not get_redis().set(AUTO_SYNC_LOCK, "1", nx=True, ex=interval_seconds):
        return False
    get_queue().enqueue(AUTO_SYNC_TASK, result_ttl=interval_seconds)
    log.info("auto_sync_queued")
    return True


def _auto_sync_loop(interval_seconds: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            queue_auto_sync(interval_seconds)
        except Exception as exc:
            log.warning("auto_sync_queue_failed", error=type(exc).__name__)
        # wake well inside the interval so a lock that just expired is picked up quickly
        stop.wait(min(60, interval_seconds))


def main() -> None:
    configure_logging()
    connection = get_redis()
    log.info("worker_starting", queue=QUEUE_NAME, environment=settings.app_env)

    stop = threading.Event()
    if settings.auto_sync_minutes > 0:
        # rq's own cron needs a separate process; a thread here avoids another service
        threading.Thread(
            target=_auto_sync_loop,
            args=(settings.auto_sync_minutes * 60, stop),
            name="auto-sync",
            daemon=True,
        ).start()

    worker = Worker([QUEUE_NAME], connection=connection)
    try:
        worker.work(with_scheduler=False)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
