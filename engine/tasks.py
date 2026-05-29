"""Background tasks for the engine app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
engine.services.enqueue_<task_name>(...) — never by importing the task
function directly.
"""
