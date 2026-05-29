"""Background tasks for the ingest app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
ingest.services.enqueue_<task_name>(...) — never by importing the task
function directly.
"""
