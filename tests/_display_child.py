"""Start the real display app inside this process, for a test that has to kill it.

tests/_browser.py's DisplayServer already serves this app over real HTTP, and
almost every display test uses it. It serves in the pytest process, though,
which is the one thing the N-7 tests cannot have: the defect under test is what
the page does when the server stops existing, and werkzeug's shutdown() leaves
the thread serving an open SSE response alive. The stream keeps delivering and
the page never notices, so a graceful stop measures nothing. This is the same
server in a process of its own, where SIGKILL takes the listening socket with
it and the page's EventSource has to see a closed connection.

WHY IT DOES NOT RUN gm-display-app.py AS A SCRIPT
=================================================
Because of one line in the app's __main__ block: `app.run(host="localhost")`.
Flask hands "localhost" to werkzeug, which hands it to http.server's
HTTPServer.server_bind(), and that does this before it ever calls listen():

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)   # bind() has happened
        host, port = self.server_address[:2]
        self.server_name = socket.getfqdn(host)    # a name resolution
        self.server_port = port

So the socket is bound but not listening while the name is being resolved, and
nothing can connect to it. On GitHub's macos-26-arm64 image that lookup does
not come back: the display sits in getfqdn for as long as it is left there. It
prints its "Flask server starting on ..." banner first, so it reads like a
server that has started.

That is why the failure looked impossible to read from the outside. #179 spent
fourteen minutes failing all fifteen of these tests on that one lookup, and
because both streams went to /dev/null, the only evidence was a RuntimeError
saying a port had stayed shut. The traceback is in the PR conversation for
94aa62a.

Binding 127.0.0.1 instead of a name removes the lookup entirely, because
getfqdn on a numeric address is a different and much cheaper call, and it is
the address DisplayServer has always used for these tests. Everything else is
untouched: the same module is loaded from the same file, so the endpoints, the
SSE stream, the seq and epoch bookkeeping and the log file this file reads back
are all the real ones.
"""
import importlib.util
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "display" / "gm-display-app.py"

# The private name matters as much as the path. The app keeps per-process state
# in _current_stats and _current_combat, and every other display test in the
# suite loads its own copy under its own name for the same reason.
MODULE_NAME = "gm_display_app_child"


def load_app(name: str = MODULE_NAME):
    """Import display/gm-display-app.py as a module, not as a script.

    exec_module rather than runpy, so the app's __main__ block does not fire.
    That block is the display's CLI entry point; a test wants the WSGI app, and
    reaching it through here is also what keeps this file off the getfqdn path
    documented above.
    """
    spec = importlib.util.spec_from_file_location(name, str(APP))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    from werkzeug.serving import make_server

    port = int(os.environ.get("GM_DISPLAY_PORT") or 0)
    server = make_server("127.0.0.1", port, load_app().app, threaded=True)
    print(f"display child serving on 127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
