# coding:utf-8
import asyncio
import logging
from typing import Dict

from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.job import Job
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.util import undefined

logger = logging.getLogger(__name__)


class DeferredScheduler:
    def __init__(self) -> None:
        self.job_args = []
        self.jobs: Dict[str, Job] = {}
        self.scheduler: BaseScheduler = None

    def start(self):
        self.scheduler = AsyncIOScheduler(event_loop=asyncio.get_event_loop())
        for job_arg in self.job_args:
            args = job_arg[:-1]
            trigger_args = job_arg[-1]
            job = self.scheduler.add_job(*args, **trigger_args)
            self.jobs.update({job.id: job})
        self.scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self):
        if self.scheduler:
            self.scheduler.shutdown()
            logger.info("Scheduler stopped")

    def scheduled_job(
        self,
        trigger,
        args=None,
        kwargs=None,
        job_id=None,
        name=None,
        misfire_grace_time=undefined,
        coalesce=undefined,
        max_instances=undefined,
        next_run_time=undefined,
        jobstore="default",
        executor="default",
        **trigger_args
    ):
        """
        装饰器: 给function添加调度任务

        :param trigger:
        :param args:
        :param kwargs:
        :param id:
        :param name:
        :param misfire_grace_time:
        :param coalesce:
        :param max_instances:
        :param next_run_time:
        :param jobstore:
        :param executor:
        :param trigger_args:
        :return:
        """

        def inner(func):
            if self.scheduler:
                job = self.scheduler.add_job(
                    func,
                    trigger,
                    args,
                    kwargs,
                    job_id,
                    name,
                    misfire_grace_time,
                    coalesce,
                    max_instances,
                    next_run_time,
                    jobstore,
                    executor,
                    True,
                    **trigger_args
                )
                self.jobs.update({job.id: job})
            else:
                self.job_args.append(
                    (
                        func,
                        trigger,
                        args,
                        kwargs,
                        job_id,
                        name,
                        misfire_grace_time,
                        coalesce,
                        max_instances,
                        next_run_time,
                        jobstore,
                        executor,
                        True,
                        trigger_args,
                    )
                )
            return func

        return inner

    def run_backgroud(self, func, args=None, kwargs=None, **trigger_args):
        if self.scheduler:
            job = self.scheduler.add_job(
                func=func, args=args, kwargs=kwargs, trigger="date", **trigger_args
            )
            self.jobs.update({job.id: job})
        else:
            self.job_args.append(
                (
                    func,
                    "date",
                    args,
                    kwargs,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    True,
                    trigger_args,
                )
            )

    def get_job(self, id: str) -> Job:
        return self.jobs.get(id, None)

    def add_job(
        self,
        job_id,
        func,
        trigger,
        args=None,
        kwargs=None,
        name=None,
        misfire_grace_time=undefined,
        coalesce=undefined,
        max_instances=undefined,
        next_run_time=undefined,
        jobstore="default",
        executor="default",
        **trigger_args
    ):
        """
        scheduler未启动时, 可以调用此方法缓存job信息
        :param id:
        :param func:
        :param trigger:
        :param args:
        :param kwargs:
        :param name:
        :param misfire_grace_time:
        :param coalesce:
        :param max_instances:
        :param next_run_time:
        :param jobstore:
        :param executor:
        :param trigger_args:
        :return:
        """
        if self.scheduler:
            job = self.scheduler.add_job(
                func,
                trigger,
                args,
                kwargs,
                job_id,
                name,
                misfire_grace_time,
                coalesce,
                max_instances,
                next_run_time,
                jobstore,
                executor,
                True,
                **trigger_args
            )
            self.jobs.update({job.id: job})
        else:
            self.job_args.append(
                (
                    func,
                    trigger,
                    args,
                    kwargs,
                    job_id,
                    name,
                    misfire_grace_time,
                    coalesce,
                    max_instances,
                    next_run_time,
                    jobstore,
                    executor,
                    True,
                    trigger_args,
                )
            )

    def remove_job(self, job_id: str):
        job = self.jobs.pop(job_id, None)
        if job:
            job.remove()


jobstores = {"default": MemoryJobStore()}

executors = {"default": ThreadPoolExecutor(20), "processpool": ProcessPoolExecutor(10)}

job_defaults = {"coalesce": False, "max_instances": 4}
