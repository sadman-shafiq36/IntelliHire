# python -m app.worker.run  (started by the worker service)

from rq import Worker

from app.config import settings
from app.logging_config import configure_logging, get_logger
from app.worker.queue import QUEUE_NAME, get_redis

log = get_logger(__name__)


def main() -> None:
    configure_logging()
    connection = get_redis()
    log.info("worker_starting", queue=QUEUE_NAME, environment=settings.app_env)

    worker = Worker([QUEUE_NAME], connection=connection)
    # nothing runs on a timer, no need for the scheduler
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
