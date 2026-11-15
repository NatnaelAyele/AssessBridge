"""Compatibility command: ensure the current PostgreSQL schema exists."""
from app.schema import initialize_schema


def migrate():
    initialize_schema()


if __name__ == '__main__':
    migrate()
    print('PostgreSQL schema ready.')
