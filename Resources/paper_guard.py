"""Own an Agent group and stop it when the app's dedicated liveness pipe closes."""
import os
import signal
import subprocess
import sys
import threading
import time


def terminate(*_):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.killpg(os.getpgrp(), signal.SIGTERM)
    time.sleep(1)
    os.killpg(os.getpgrp(), signal.SIGKILL)


def watch(fd):
    while os.read(fd, 1):
        pass
    # Python signal handlers must run on the main thread.
    os.kill(os.getpid(), signal.SIGTERM)


if __name__ == '__main__':
    control = int(sys.argv[1])
    args = sys.argv[2:]
    status_path = None
    if args[:1] == ['--exit-status']:
        status_path, args = args[1], args[2:]
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    threading.Thread(target=watch, args=(control,), daemon=True).start()
    child = subprocess.Popen(args, close_fds=True)
    code = child.wait()
    if status_path:
        fd = os.open(status_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(str(code)); output.flush(); os.fsync(output.fileno())
    # Kill any grandchildren left after the Agent exits, including on normal completion.
    terminate()
