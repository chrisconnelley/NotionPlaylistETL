import logging
import queue

log_queue: queue.Queue = queue.Queue()


class _QueueHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        log_queue.put(self.format(record))


def _setup() -> logging.Logger:
    logger = logging.getLogger("etl")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    qh = _QueueHandler()
    qh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(qh)
    return logger


log = _setup()
