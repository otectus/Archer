"""Bounded subprocess execution shared by the daemon and privileged helpers."""

from dataclasses import dataclass
import os
import selectors
import signal
import time
import subprocess


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ''
    stderr: str = ''
    timed_out: bool = False

    @property
    def success(self):
        return self.returncode == 0 and not self.timed_out


def run_command(arguments, timeout=30):
    if not isinstance(arguments, (tuple, list)):
        raise TypeError('Commands require an argument list')
    try:
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as error:
        return CommandResult(127, stderr=str(error))

    output = [bytearray(), bytearray()]
    size = 0
    deadline = time.monotonic() + timeout
    failure = None
    with process, selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, 0)
        selector.register(process.stderr, selectors.EVENT_READ, 1)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "Command timed out"
                break
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    failure = "Command exceeded the output limit"
                    break
                output[key.data].extend(chunk)
            if failure:
                break
        if not failure:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                failure = "Command timed out"
        if failure:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            return CommandResult(1, stderr=failure, timed_out=failure == "Command timed out")
    stdout, stderr = (data.decode(errors="replace").strip() for data in output)
    return CommandResult(process.returncode, stdout, stderr)
