import logging

logger = logging.getLogger(__name__)


def process_reviews() -> None:
    logger.info("review_worker_started")
