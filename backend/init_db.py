"""Create an empty PostgreSQL database and its schema. Never imports old records."""
import psycopg
from psycopg import sql
from app.config import DB_CONFIG
from app.schema import initialize_schema


def main():
    config = {**DB_CONFIG, 'dbname': 'postgres'}
    database_name = DB_CONFIG['dbname']
    with psycopg.connect(**config, autocommit=True) as connection:
        if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (database_name,)).fetchone():
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    initialize_schema()
    print('PostgreSQL schema ready. No accounts or assessment records were imported.')


if __name__ == '__main__':
    main()
