import logging

logger = logging.getLogger(__name__)


def process_posting_queue() -> None:
    logger.info("posting_worker_started")
