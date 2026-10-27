from celery.beat import PersistentScheduler
from app.services.storage import redis
from app.models import now


class HealthScheduler(PersistentScheduler):
    def tick(self, *args, **kwargs):
        redis.set("scheduler-live", now().isoformat(), ex=120)
        return super().tick(*args, **kwargs)
