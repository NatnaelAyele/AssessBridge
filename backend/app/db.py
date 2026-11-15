from contextlib import contextmanager

import mysql.connector

from .config import DB_CONFIG


@contextmanager
def database():
    # MySQL converts TIMESTAMP columns using the connection timezone. Keep
    # those reads/defaults aligned with our UTC DATETIME fields and API times,
    # regardless of the database host's operating-system timezone.
    connection = mysql.connector.connect(**DB_CONFIG, time_zone="+00:00")
    try:
        yield connection
    finally:
        connection.close()
