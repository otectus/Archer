"""Bounded subprocess execution shared by the daemon and privileged helpers."""

from dataclasses import dataclass
import os
import signal
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
                                   text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return CommandResult(process.returncode, stdout.strip(), stderr.strip())
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            return CommandResult(process.returncode, stdout.strip(), stderr.strip(), True)
    except OSError as error:
        return CommandResult(127, stderr=str(error))
