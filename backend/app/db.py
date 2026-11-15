"""PostgreSQL connections with UTC timestamps and the application's row interface."""
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import set_json_loads
from .config import DB_CONFIG


class Cursor(psycopg.Cursor):
    """Expose IDs explicitly returned by INSERT statements to existing callers."""
    lastrowid = None

    def execute(self, query, params=None, **kwargs):
        self.lastrowid = None
        super().execute(query, params, **kwargs)
        if self.statusmessage and self.statusmessage.startswith('INSERT') and self.description:
            row = self.fetchone()
            if row is not None:
                self.lastrowid = row['id'] if isinstance(row, dict) else row[0]
        return self


class Connection(psycopg.Connection):
    def cursor(self, *args, dictionary=False, **kwargs):
        kwargs.setdefault('row_factory', dict_row if dictionary else tuple_row)
        return super().cursor(*args, **kwargs)


@contextmanager
def database():
    connection = Connection.connect(**DB_CONFIG, options='-c timezone=UTC', cursor_factory=Cursor)
    # Keep the JSON text interface used by domain serializers while storing JSONB.
    set_json_loads(lambda value: bytes(value).decode('utf-8'), connection)
    try:
        yield connection
    finally:
        # No implicit commit: failed invitation batches must roll back completely.
        connection.close()
