"""Background tasks for the corrections app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
corrections.services.enqueue_<task_name>(...) — never by importing the task
function directly.
"""
