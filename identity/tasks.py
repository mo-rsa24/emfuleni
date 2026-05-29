"""Background tasks for the identity app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
identity.services.enqueue_<task_name>(...) — never by importing the task
function directly.
"""
