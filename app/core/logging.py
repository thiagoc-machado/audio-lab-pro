import json
import logging
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    def format(self, record):
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            result["exception"] = self.formatException(record.exc_info)
        return json.dumps(result)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
