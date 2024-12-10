# coding:utf-8
import sched
import threading
import time
from typing import Dict, Optional

from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.job import Job
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.util import undefined

from chatbot.robot import log
from chatbot.robot.compt import ThreadManager

logger = log.getLogger(__name__)


class SimpleScheduler:

    def __init__(self) -> None:
        # 创建 scheduler 实例
        self.scheduler = sched.scheduler(time.time, time.sleep)
        self.thread = ThreadManager.new(target=self.scheduler.run)

    def add_job(self, action, interval, priority=1, *args, **kwargs):
        def wrapper():
            try:
                action(*args, **kwargs)
            finally:
                self.add_job(action=action, interval=interval, priority=priority, *args, **kwargs)

        self.scheduler.enter(delay=interval, priority=priority, action=wrapper)

    def start(self):
        self.thread.start()

    def clear(self):
        for job in self.scheduler.queue:
            self.scheduler.cancel(job)

    def empty(self):
        self.scheduler.empty()


class DeferredScheduler:
    def __init__(self) -> None:
        self.job_args = []
        self.jobs: Dict[str, Job] = {}
        self.scheduler: Optional[BaseScheduler] = None

    def start(self):
        self.scheduler = BackgroundScheduler(gconfig=scheduler_config)
        for job_arg in self.job_args:
            args = job_arg[:-1]
            trigger_args = job_arg[-1]
            job = self.scheduler.add_job(*args, **trigger_args)
            self.jobs.update({job.id: job})
        self.scheduler.start()
        logger.info("Scheduler started")

    def stop(self):
        if self.scheduler:
            self.scheduler.shutdown()
        logger.info("Scheduler stopped")

    def add_job(self, id, func, trigger, args=None, kwargs=None, name=None,
                misfire_grace_time=undefined, coalesce=undefined, max_instances=undefined,
                next_run_time=undefined, jobstore='default', executor='default',
                **trigger_args):
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
            job = self.scheduler.add_job(func, trigger, args, kwargs, id, name, misfire_grace_time, coalesce,
                                         max_instances, next_run_time, jobstore, executor, True,
                                         **trigger_args)
            self.jobs.update({job.id: job})
        else:
            self.job_args.append((func, trigger, args, kwargs, id, name, misfire_grace_time, coalesce,
                                  max_instances, next_run_time, jobstore, executor, True, trigger_args))

    def wrap_job(self, trigger, args=None, kwargs=None, id=None, name=None,
                 misfire_grace_time=undefined, coalesce=undefined, max_instances=undefined,
                 next_run_time=undefined, jobstore='default', executor='default',
                 **trigger_args):
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
            self.add_job(id=id, func=func, trigger=trigger, args=args, kwargs=kwargs, name=name,
                         misfire_grace_time=misfire_grace_time, coalesce=coalesce, max_instances=max_instances,
                         next_run_time=next_run_time, jobstore=jobstore, executor=executor, **trigger_args)
            return func

        return inner

    def get_job(self, id: str) -> Job:
        return self.jobs.get(id, None)

    def remove_job(self, id: str):
        job = self.jobs.pop(id, None)
        if job:
            job.remove()


scheduler_config = {
    "jobstores": {
        'default': MemoryJobStore()
    },

    "executors": {
        'default': ThreadPoolExecutor(3),
        'processpool': ProcessPoolExecutor(1)
    },
    "job_defaults": {
        'coalesce': False,
        'max_instances': 3
    }
}
