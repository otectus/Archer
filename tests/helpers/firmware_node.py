"""Fake Linuwu-Sense 0/1 node for hardware-free tests (issue #10).

By default writes are accepted while readback stays "-1", as reported on the
PT516-52S. echo=True models an ordinary device that reads back its last value.
"""


class FirmwareNode:
    def __init__(self, readback='-1\n', echo=False):
        self.readback = readback  # text, or an OSError to report the node as unreadable
        self.echo = echo
        self.present = True
        self.generation = 1  # changes when a driver reload recreates the node
        self.write_error = None
        self.writes = []
        self.reads = 0

    def exists(self, path):
        return self.present

    def identity(self, path):
        return ('sysfs', self.generation) if self.present else None

    def read(self, path):
        self.reads += 1
        if not self.present:
            return None, 'missing'
        if isinstance(self.readback, OSError):
            return None, self.readback.strerror
        return self.readback, None

    def write(self, path, text):
        if not self.present:
            return 'the control interface is missing'
        if self.write_error:
            return self.write_error
        self.writes.append(text)
        if self.echo:
            self.readback = text + '\n'
        return None
