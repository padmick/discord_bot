import logging
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

handler = RotatingFileHandler('secret_santa.log', maxBytes=1000000, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(message)s'))

logger.addHandler(handler)

def log_event(event_type: str, description: str):
    logger.info(f"{event_type}: {description}")
