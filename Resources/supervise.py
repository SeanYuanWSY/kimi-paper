"""Keep a service group tied to the lifetime of the app's private stdin pipe."""
import os
import selectors
import signal
import subprocess
import sys
import time


def main():
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.buffer, selectors.EVENT_READ)
    try:
        while not stopping and child.poll() is None:
            if selector.select(timeout=0.25) and not os.read(sys.stdin.fileno(), 1024):
                break
    finally:
        selector.close()
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 3
        # Wait for the owned group, not only its leader. A grandchild may ignore TERM.
        while time.monotonic() < deadline:
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
    return child.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
