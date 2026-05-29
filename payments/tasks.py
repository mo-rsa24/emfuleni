"""Background tasks for the payments app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
payments.services.enqueue_<task_name>(...) — never by importing the task
function directly.
"""
