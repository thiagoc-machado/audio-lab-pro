import sys
from app.services.storage import redis

sys.exit(0 if redis.exists("scheduler-live") else 1)
