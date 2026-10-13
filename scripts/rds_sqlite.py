"""SQLite I/O primitives without project policy or writer callbacks."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


@contextmanager
def readonly_connection(path):
    """Open a separate committed-state reader; never borrow a pending writer.

    The caller owns path/identity checks and any explicit read transaction.
    Connection settings match the legacy ProjectStore readonly connection.
    This API does not change journal mode, checkpoint, or migrate a database.
    """
    db = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        with db:
            yield db
    finally:
        db.close()
