"""Start the real display app inside this process, for a test that has to kill it.

tests/_browser.py's DisplayServer already serves this app over real HTTP, and
almost every display test uses it. It serves in the pytest process, though,
which is the one thing the N-7 tests cannot have: the defect under test is what
the page does when the server stops existing, and werkzeug's shutdown() leaves
the thread serving an open SSE response alive. The stream keeps delivering and
the page never notices, so a graceful stop measures nothing. This is the same
server in a process of its own, where SIGKILL takes the listening socket with
it and the page's EventSource has to see a closed connection.

WHY THE SOCKET IS BOUND HERE AND NOT BY werkzeug
=================================================
Because http.server's HTTPServer.server_bind() calls socket.getfqdn() between
bind() and listen(), and on GitHub's macos-26-arm64 image that call does not
come back. It is the whole of #179's fifteen failures, twice over, and the
traceback is unambiguous about the frame:

    File ".../http/server.py", line 142 in server_bind
    File ".../werkzeug/serving.py", line 763 in __init__
    File ".../werkzeug/serving.py", line 934 in make_server

server_bind is where the listening socket is supposed to be finished off, and
this is the order http.server uses:

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)   # bind() has happened
        host, port = self.server_address[:2]
        self.server_name = socket.getfqdn(host)    # a name lookup, and it blocks
        self.server_port = port

listen() is called by server_activate(), which runs after this. So for as long
as the lookup is outstanding the socket is bound and not listening, and nothing
can connect to it. The test's report says "127.0.0.1: timed out" rather than
"connection refused" for exactly that reason, and the distinction is the whole
diagnosis: refused means nothing is bound, timed out means something is.

It is not a matter of passing a numeric address instead of a name. The first
version of this file bound 127.0.0.1 and the same fifteen tests failed the same
way, in the same frame, with the same "timed out". Whatever makes the lookup
slow on that image, it is not the argument to getfqdn.

So the socket is built here, where nothing resolves a name, and werkzeug is
handed the finished descriptor:

    sock = socket.create_server(("127.0.0.1", port))   # bind() then listen()
    server = make_server("127.0.0.1", port, app, threaded=True, fd=sock.fileno())

create_server is those two syscalls and a SO_REUSEADDR, which also matters
because restart() rebinds the same port. make_server's fd path calls
TCPServer with bind_and_activate=False and then takes the descriptor with
socket.fromfd, so server_bind() and getfqdn() are never reached. The only
things server_bind would have set are server_name and server_port, and
server_port is set from getsockname() on the fd path anyway; server_name is
not read anywhere in werkzeug.serving, so there is nothing to lose.

tests/_display_child.py therefore does the same job as app.run() and not the
same work: the same WSGI app from the same file, so the endpoints, the SSE
stream, the seq and epoch bookkeeping and the log file these tests read back
are all the real ones. It also stops writing display/.scheme, which the app's
__main__ block did on every start.

display/ is deliberately untouched. app.run(host="localhost") blocking startup
on a name lookup, after printing a banner that says the server has started, is
a real bug in the app and it is reported on its own rather than fixed here.
"""
import importlib.util
import os
import pathlib
import socket
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


def listening_socket(port: int) -> socket.socket:
    """A bound, listening loopback socket, with no name lookup anywhere in it."""
    return socket.create_server(("127.0.0.1", port))


def main() -> int:
    from werkzeug.serving import make_server

    requested = int(os.environ.get("GM_DISPLAY_PORT") or 0)
    sock = listening_socket(requested)
    try:
        server = make_server("127.0.0.1", requested, load_app().app,
                             threaded=True, fd=sock.fileno())
    finally:
        # make_server dup()s the descriptor, so this one is spent. Leaving it
        # open would hold a second reference to the listening socket, and the
        # point of this process is that killing it is what closes the port.
        sock.close()
    print(f"display child serving on 127.0.0.1:{server.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
