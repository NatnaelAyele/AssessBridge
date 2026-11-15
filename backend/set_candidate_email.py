"""Assign a trusted email address to an existing candidate account."""

import argparse
import re

from app.db import database


parser = argparse.ArgumentParser(description="Set a candidate account's registered email")
parser.add_argument("username")
parser.add_argument("email")
args = parser.parse_args()
email = args.email.strip().lower()
if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
    parser.error("Enter a valid email address")

with database() as connection:
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE username = %s AND role = 'candidate'", (args.username,))
    user = cursor.fetchone()
    if not user:
        parser.error("Candidate account not found")
    cursor.execute(
        """SELECT COUNT(*) AS conflicts FROM assessment_invitations
           WHERE candidate_user_id = %s AND candidate_email <> %s""",
        (user["id"], email),
    )
    if cursor.fetchone()["conflicts"]:
        parser.error("This account already has invitations for another email address")
    cursor.execute("UPDATE users SET email = %s WHERE id = %s", (email, user["id"]))
    connection.commit()
    cursor.close()

print(f"Registered email for {args.username}: {email}")
