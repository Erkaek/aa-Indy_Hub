"""
Performance optimization utilities for Celery tasks.
Includes deduplication, batching, and metrics collection.
"""

# Standard Library
import logging
from datetime import timedelta
from functools import wraps

# Django
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


def deduplicate_location_ids(cache_key="pending_location_ids", timeout=5):
    """
    Decorator to deduplicate location IDs before enqueueing async task.
    Accumulates IDs in cache and flushes periodically.
    
    Example:
        @deduplicate_location_ids("blueprint_locations", timeout=3)
        def enqueue_location_population(location_ids):
            populate_location_names_async.delay(location_ids)
    
    Args:
        cache_key: Redis key to store pending IDs
        timeout: Seconds to wait before forced flush
    """
    def decorator(func):
        @wraps(func)
        def wrapper(location_ids=None, force_flush=False, *args, **kwargs):
            if not location_ids:
                location_ids = []
            
            # Normalize to list of integers
            location_ids = list(set(int(lid) for lid in location_ids if lid))
            
            if not location_ids and not force_flush:
                logger.debug("No location IDs provided, skipping deduplication")
                return
            
            # Get pending IDs from cache
            pending = cache.get(cache_key, set())
            if isinstance(pending, list):
                pending = set(pending)
            
            # Add new IDs
            pending.update(location_ids)
            
            # Decide whether to flush
            should_flush = (
                force_flush or 
                len(pending) > 50 or  # Flush if accumulated more than 50
                not location_ids  # Flush if called with no args (periodic)
            )
            
            if should_flush and pending:
                logger.info(
                    "Flushing %d deduplicated location IDs via %s",
                    len(pending),
                    func.__name__,
                )
                # Call original function with deduplicated IDs
                result = func(list(pending), *args, **kwargs)
                cache.delete(cache_key)
                return result
            else:
                # Cache pending IDs for next call
                cache.set(cache_key, pending, timeout)
                logger.debug(
                    "Cached %d location IDs (total pending: %d)",
                    len(location_ids),
                    len(pending),
                )
                return None
        
        return wrapper
    return decorator


class TaskMetrics:
    """
    Simple metrics collection for Celery tasks.
    Tracks execution time, success/failure counts, queue depth.
    """
    
    METRICS_KEY = "celery_task_metrics"
    
    @classmethod
    def record_start(cls, task_name):
        """Record task start time and increment counter."""
        metrics = cache.get(cls.METRICS_KEY, {})
        
        if task_name not in metrics:
            metrics[task_name] = {
                "count": 0,
                "failures": 0,
                "total_duration_ms": 0,
                "last_run": None,
                "avg_duration_ms": 0,
            }
        
        metrics[task_name]["count"] += 1
        metrics[task_name]["last_run"] = timezone.now().isoformat()
        cache.set(cls.METRICS_KEY, metrics, 3600)  # 1 hour TTL
        
        return timezone.now()
    
    @classmethod
    def record_end(cls, task_name, start_time, success=True, duration_ms=None):
        """Record task completion and duration."""
        metrics = cache.get(cls.METRICS_KEY, {})
        
        if task_name not in metrics:
            metrics[task_name] = {
                "count": 0,
                "failures": 0,
                "total_duration_ms": 0,
                "last_run": None,
                "avg_duration_ms": 0,
            }
        
        if not success:
            metrics[task_name]["failures"] += 1
        
        if duration_ms is None:
            duration_ms = int((timezone.now() - start_time).total_seconds() * 1000)
        
        metrics[task_name]["total_duration_ms"] += duration_ms
        metrics[task_name]["avg_duration_ms"] = (
            metrics[task_name]["total_duration_ms"] / 
            max(metrics[task_name]["count"], 1)
        )
        
        cache.set(cls.METRICS_KEY, metrics, 3600)
        
        log_level = logging.WARNING if not success else logging.INFO
        logger.log(
            log_level,
            "Task %s completed in %dms (avg: %dms, failures: %d/%d)",
            task_name,
            duration_ms,
            int(metrics[task_name]["avg_duration_ms"]),
            metrics[task_name]["failures"],
            metrics[task_name]["count"],
        )
    
    @classmethod
    def get_metrics(cls, task_name=None):
        """Get metrics for a specific task or all tasks."""
        metrics = cache.get(cls.METRICS_KEY, {})
        
        if task_name:
            return metrics.get(task_name, {})
        
        return metrics
    
    @classmethod
    def reset(cls):
        """Reset all metrics."""
        cache.delete(cls.METRICS_KEY)
        logger.info("Task metrics reset")


def with_metrics(task_name=None):
    """
    Decorator to add metrics collection to a Celery task.
    Compatible with both regular and bind=True tasks.
    
    Example:
        @shared_task
        @with_metrics("my_task")
        def my_task():
            pass
        
        @shared_task(bind=True)
        @with_metrics("my_bound_task")
        def my_bound_task(self):
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            name = task_name or func.__name__
            start_time = TaskMetrics.record_start(name)
            
            try:
                result = func(*args, **kwargs)
                TaskMetrics.record_end(name, start_time, success=True)
                return result
            except Exception as exc:
                TaskMetrics.record_end(name, start_time, success=False)
                raise
        
        return wrapper
    return decorator


def with_timeout_handler(timeout_seconds=300):
    """
    Decorator to gracefully handle task timeouts.
    Logs timeout events for monitoring.
    
    Example:
        @shared_task(time_limit=300)
        @with_timeout_handler(300)
        def long_task():
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Note: This is a best-effort decorator.
            # For actual timeout handling in Celery, use SoftTimeLimitExceeded
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                if "SoftTimeLimitExceeded" in str(type(exc).__name__):
                    logger.warning(
                        "Task %s exceeded soft time limit (%ds)",
                        func.__name__,
                        timeout_seconds,
                    )
                raise
        
        return wrapper
    return decorator
