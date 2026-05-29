"""Background tasks for the vlm app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
vlm.services.enqueue_<task_name>(...) — never by importing the task
function directly.
"""
