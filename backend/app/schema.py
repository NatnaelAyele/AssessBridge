"""Idempotent schema setup for a fresh PostgreSQL installation."""
from pathlib import Path
from .db import database

SCHEMA_DIR = Path(__file__).resolve().parents[1]
FILES = ('schema.sql', 'casework_schema.sql', 'reporting_schema.sql',
         'readiness_schema.sql', 'retention_schema.sql', 'esignet_schema.sql')


def initialize_schema():
    with database() as connection:
        cursor = connection.cursor()
        cursor.execute('CREATE EXTENSION IF NOT EXISTS citext')
        company_statements = (SCHEMA_DIR / 'company_schema.sql').read_text(encoding='utf-8').split(';')
        # Users reference companies; staff invitations reference users.
        cursor.execute(company_statements[0])
        for name in FILES:
            for statement in (SCHEMA_DIR / name).read_text(encoding='utf-8').split(';'):
                if statement.strip():
                    cursor.execute(statement)
        for statement in company_statements[1:]:
            if statement.strip():
                cursor.execute(statement)
        connection.commit()
