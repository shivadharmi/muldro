"""Cross-thread runtime signals shared between the API lifespan and the worker.

This module exists to avoid a subtle double-import bug: ``run.py`` is executed as
``__main__`` (``python run.py``), so any object defined there lives on the
``__main__`` module. When ``src.api.app`` does ``from run import X`` it imports
``run.py`` a *second* time as the module ``run`` — a distinct object from
``__main__`` — so the two halves would reference different ``Event`` instances and
the handshake could never complete. Keeping the shared signal in a normal ``src.``
module means every importer (``__main__`` worker, lifespan, tests) binds the *same*
object.

NOTE: a ``threading.Event`` only synchronises threads within ONE process. Under
``uvicorn --reload`` the API lifespan runs in a child process while the worker runs
in the reloader parent, so this Event cannot bridge them — ``run.py`` detects reload
mode and skips the wait instead of timing out.
"""

import threading

# Set by the FastAPI lifespan once the MCP session pool is wired; waited on by the
# worker thread before it begins processing tasks (single-process mode only).
mcp_bridge_ready = threading.Event()
