from pathlib import Path

import mysql.connector

from app.config import DB_CONFIG


schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
server_config = {key: value for key, value in DB_CONFIG.items() if key != "database"}
database_name = DB_CONFIG["database"]

if not database_name.replace("_", "").isalnum():
    raise ValueError("Invalid database name")

with mysql.connector.connect(**server_config) as connection:
    cursor = connection.cursor()
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cursor.close()

with mysql.connector.connect(**DB_CONFIG) as connection:
    cursor = connection.cursor()
    for statement in schema.split(";"):
        if statement.strip():
            cursor.execute(statement)
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email'""",
        (database_name,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE users ADD COLUMN email VARCHAR(254) COLLATE utf8mb4_unicode_ci NULL UNIQUE")
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'assessment_invitations'
             AND COLUMN_NAME = 'candidate_user_id'""",
        (database_name,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """ALTER TABLE assessment_invitations
               ADD COLUMN candidate_user_id BIGINT UNSIGNED NULL,
               ADD CONSTRAINT fk_invitation_candidate_user
                   FOREIGN KEY (candidate_user_id) REFERENCES users(id)"""
        )
    for column_name, definition in (
        ("seb_config_key", "CHAR(64) NULL"),
        ("seb_config_blob", "LONGBLOB NULL"),
    ):
        cursor.execute(
            """SELECT COUNT(*) FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'assessment_invitations' AND COLUMN_NAME = %s""",
            (database_name, column_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE assessment_invitations ADD COLUMN {column_name} {definition}")
    for column_name, definition in (
        ("auth_session_token_id", "CHAR(64) NULL"),
        ("last_seen_at", "DATETIME NULL"),
        ("disqualified_at", "DATETIME NULL"),
        ("disqualification_reason", "ENUM('early_exit', 'connection_lost') NULL"),
        ("interrupted_at", "DATETIME NULL"),
        ("interruption_reason", "VARCHAR(40) NULL"),
        ("recovery_started_at", "DATETIME(3) NULL"),
        ("recovery_reason", "VARCHAR(40) NULL"),
        ("recovery_seconds", "DECIMAL(12,3) NOT NULL DEFAULT 0"),
        ("client_instance_id", "VARCHAR(36) NULL"),
        ("resume_required", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("resume_identity_client", "VARCHAR(36) NULL"),
    ):
        cursor.execute(
            """SELECT COUNT(*) FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'assessment_exam_sessions' AND COLUMN_NAME = %s""",
            (database_name, column_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE assessment_exam_sessions ADD COLUMN {column_name} {definition}")
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'assessment_identity_checks'
             AND COLUMN_NAME = 'access_fingerprint'""",
        (database_name,),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE assessment_identity_checks ADD COLUMN access_fingerprint CHAR(64) NULL")
    for column_name, definition in (
        ("stage", "VARCHAR(30) NULL"),
        ("card_detection_method", "VARCHAR(30) NULL"),
        ("name_readability", "DECIMAL(5,4) NULL"),
    ):
        cursor.execute(
            """SELECT COUNT(*) FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'assessment_identity_checks' AND COLUMN_NAME = %s""",
            (database_name, column_name),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE assessment_identity_checks ADD COLUMN {column_name} {definition}")
    # Existing attempts predate heartbeat monitoring. Start their lease at migration time.
    cursor.execute("UPDATE assessment_exam_sessions SET last_seen_at = UTC_TIMESTAMP() WHERE last_seen_at IS NULL AND submitted_at IS NULL")
    # Preserve the owner of any attempt that was already started before this migration.
    cursor.execute(
        """UPDATE assessment_invitations i
           JOIN assessment_exam_sessions s ON s.invitation_id = i.id
           SET i.candidate_user_id = s.candidate_user_id
           WHERE i.candidate_user_id IS NULL"""
    )
    cursor.execute(
        """UPDATE assessment_invitations i
           JOIN (
               SELECT invitation_id, MIN(candidate_user_id) AS candidate_user_id
               FROM assessment_consents
               GROUP BY invitation_id
               HAVING COUNT(DISTINCT candidate_user_id) = 1
           ) c ON c.invitation_id = i.id
           SET i.candidate_user_id = c.candidate_user_id
           WHERE i.candidate_user_id IS NULL"""
    )
    cursor.execute(
        """UPDATE assessment_invitations i
           JOIN users u ON i.candidate_email COLLATE utf8mb4_unicode_ci = u.email
           SET i.candidate_user_id = u.id
           WHERE i.candidate_user_id IS NULL AND u.role = 'candidate' AND u.is_active = TRUE"""
    )
    connection.commit()
    cursor.close()

with mysql.connector.connect(**DB_CONFIG) as connection:
    cursor = connection.cursor()
    cursor.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
                      WHERE TABLE_SCHEMA=%s AND TABLE_NAME='assessment_result_publications' AND COLUMN_NAME='is_legacy'""", (database_name,))
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE assessment_result_publications ADD COLUMN is_legacy BOOLEAN NOT NULL DEFAULT FALSE")
    # Preserve earlier publications and snapshots, retaining the last publication
    # as the sole final entry visible in history and to candidates.
    cursor.execute("""UPDATE assessment_result_publications earlier
                      JOIN assessment_result_publications newer
                      ON newer.assessment_id=earlier.assessment_id AND newer.id>earlier.id
                      SET earlier.is_legacy=TRUE WHERE earlier.is_legacy=FALSE""")
    cursor.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
                      WHERE TABLE_SCHEMA=%s AND TABLE_NAME='assessment_result_publications' AND COLUMN_NAME='final_assessment_id'""", (database_name,))
    if cursor.fetchone()[0] == 0:
        cursor.execute("""ALTER TABLE assessment_result_publications
                          ADD COLUMN final_assessment_id BIGINT UNSIGNED GENERATED ALWAYS AS
                          (CASE WHEN is_legacy=FALSE THEN assessment_id ELSE NULL END) VIRTUAL,
                          ADD UNIQUE KEY uq_final_publication_assessment(final_assessment_id)""")
    connection.commit()
    cursor.close()

print("Database schema is ready.")

from migrate_companies import migrate
migrate()
