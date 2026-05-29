"""Background tasks for the common app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
common.services.enqueue_<task_name>(...) — never by importing the task
function directly.
"""
