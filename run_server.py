"""
MshibeGroup + FixFlow — one server, two apps.

  http://127.0.0.1:5000/            ->  MshibeGroup
  http://127.0.0.1:5000/fixflow/    ->  FixFlow

Run:
    python run_server.py
"""

import os
import sys
import importlib.util

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple

BASE = os.path.dirname(os.path.abspath(__file__))
FIXFLOW_DIR = os.path.join(BASE, "fixflow")
FIXFLOW_PREFIX = "/apps/fixflow"
HOST = "127.0.0.1"
PORT = 5000
DEBUG = True


def load_module(name, path):
    """Load a module from an arbitrary path under a unique name."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Not found: {path}")
    spec = importlib.util.spec_from_file_location(
        name,
        path,
        submodule_search_locations=[os.path.dirname(path)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    # ---- 1. load MshibeGroup ----
    mshibe_mod = load_module("mshibe_app", os.path.join(BASE, "app.py"))
    mshibe_app = getattr(mshibe_mod, "app", None)
    if mshibe_app is None:
        raise RuntimeError("MshibeGroup's app.py has no `app` object.")

    # ---- 2. load FixFlow ----
    fixflow_mod = load_module("fixflow_app", os.path.join(FIXFLOW_DIR, "app.py"))
    fixflow_app = getattr(fixflow_mod, "app", None)
    if fixflow_app is None:
        raise RuntimeError("FixFlow's app.py has no `app` object.")

    # ---- 3. isolate FixFlow's session so the two apps don't fight ----
    fixflow_app.config["SESSION_COOKIE_NAME"] = "fixflow_session"
    fixflow_app.config["SESSION_COOKIE_PATH"] = FIXFLOW_PREFIX
    fixflow_app.config["APPLICATION_ROOT"] = FIXFLOW_PREFIX
    fixflow_app.config.setdefault("SECRET_KEY", "fixflow-dev-secret-change-me")

    # ---- 4. mount ----
    combined = DispatcherMiddleware(mshibe_app, {
        FIXFLOW_PREFIX: fixflow_app,
    })

    print()
    print("  MshibeGroup  ->  http://%s:%d/" % (HOST, PORT))
    print("  FixFlow      ->  http://%s:%d%s/" % (HOST, PORT, FIXFLOW_PREFIX))
    print()
    print("  Press Ctrl+C to stop.")
    print()

    run_simple(
        HOST,
        PORT,
        combined,
        use_reloader=DEBUG,
        use_debugger=DEBUG,
        threaded=True,
    )


if __name__ == "__main__":
    main()