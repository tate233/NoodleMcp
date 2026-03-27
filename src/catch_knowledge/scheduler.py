from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from catch_knowledge.config import Settings
from catch_knowledge.pipeline import run_pipeline


def run_scheduler(settings: Settings) -> None:
    scheduler = BlockingScheduler(timezone=settings.timezone)
    trigger = CronTrigger.from_crontab(settings.schedule_cron, timezone=settings.timezone)
    scheduler.add_job(run_pipeline, trigger=trigger, args=[settings], id="daily_nowcoder_pipeline")
    scheduler.start()
