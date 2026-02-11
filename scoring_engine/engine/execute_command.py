from scoring_engine.celery_app import celery_app
from celery.exceptions import SoftTimeLimitExceeded
import subprocess

from scoring_engine.logger import logger


from scoring_engine.celery_app import celery_app
from celery.exceptions import SoftTimeLimitExceeded
import subprocess

from scoring_engine.logger import logger


@celery_app.task(name='execute_command', bind=True, acks_late=True, reject_on_worker_lost=True, soft_time_limit=30, max_retries=3, default_retry_delay=30)
def execute_command(self, job): 
    output = ""
    # Disable duplicate celery log messages
    if logger.propagate:
        logger.propagate = False
    
    logger.info(
        "Running command",
        extra={
            "job": str(job),
            "retry_count": self.request.retries,
            "task_id": self.request.id
        }
    )
    
    try:
        cmd_result = subprocess.run(
            job['command'],
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        output = cmd_result.stdout.decode("utf-8")
        job['errored_out'] = False
        
    except SoftTimeLimitExceeded:
        # Task timed out - retry with exponential backoff
        job['errored_out'] = True
        countdown = 30 * (2 ** self.request.retries)  # Exponential backoff: 30s, 60s, 120s
        
        logger.warning(
            "Task timed out, retrying with backoff",
            extra={
                "retry_count": self.request.retries,
                "max_retries": 3,
                "countdown_seconds": countdown,
                "task_id": self.request.id,
                "job": str(job)
            }
        )
        
        # Retry the task
        raise self.retry(countdown=countdown, max_retries=3)
        
    except subprocess.SubprocessError as exc:
        # Command execution failed - retry with exponential backoff
        job['errored_out'] = True
        countdown = 30 * (2 ** self.request.retries)
        
        logger.warning(
            "Command execution failed, retrying with backoff",
            extra={
                "retry_count": self.request.retries,
                "max_retries": 3,
                "countdown_seconds": countdown,
                "task_id": self.request.id,
                "job": str(job),
                "error": str(exc)
            }
        )
        
        # Retry the task
        raise self.retry(exc=exc, countdown=countdown, max_retries=3)
        
    except Exception as exc:
        # Unexpected error - retry with exponential backoff
        job['errored_out'] = True
        countdown = 30 * (2 ** self.request.retries)
        
        logger.error(
            "Unexpected error, retrying with backoff",
            extra={
                "retry_count": self.request.retries,
                "max_retries": 3,
                "countdown_seconds": countdown,
                "task_id": self.request.id,
                "job": str(job),
                "error": str(exc)
            }
        )
        
        # Retry the task
        raise self.retry(exc=exc, countdown=countdown, max_retries=3)
    
    job['output'] = output
    
    logger.info(
        "Command completed",
        extra={
            "task_id": self.request.id,
            "errored_out": job['errored_out'],
            "retry_count": self.request.retries
        }
    )
    
    return job
