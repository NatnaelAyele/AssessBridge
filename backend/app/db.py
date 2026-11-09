from contextlib import contextmanager

import mysql.connector

from .config import DB_CONFIG


@contextmanager
def database():
    connection = mysql.connector.connect(**DB_CONFIG)
    try:
        yield connection
    finally:
        connection.close()
