"""Read a bounded regular file through one nonblocking, checked descriptor."""
import os
import stat


def read_regular_bytes(path, max_bytes, *, label='Input'):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('Regular-file byte bound must be a positive integer')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(label + ' must be a regular file')
        if info.st_size > max_bytes:
            raise ValueError(label + ' exceeds its byte bound')
        raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(label + ' exceeds its byte bound')
        return raw
