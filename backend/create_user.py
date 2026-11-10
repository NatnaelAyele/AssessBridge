import argparse
import getpass
import re

from app.db import database
from app.security import hash_password


parser = argparse.ArgumentParser(description="Create an AssessBridge user")
parser.add_argument("username")
parser.add_argument("role", choices=["candidate", "employer_personnel", "auditor"])
parser.add_argument("--email", help="Registered email address (required for candidates)")
args = parser.parse_args()

username = args.username.strip()
if not username or len(username) > 100:
    parser.error("Username must be 1 to 100 characters")
email = args.email.strip().lower() if args.email else None
if args.role == "candidate" and not email:
    parser.error("Candidate accounts require --email matching their invitation address")
if email and (len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)):
    parser.error("Enter a valid email address")

password = getpass.getpass("Password: ")
confirmation = getpass.getpass("Confirm password: ")
if password != confirmation:
    parser.error("Passwords do not match")
if len(password) < 12:
    parser.error("Password must be at least 12 characters")

password_hash = hash_password(password)
with database() as connection:
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, %s)",
        (username, email, password_hash, args.role),
    )
    connection.commit()
    cursor.close()

print(f"Created {args.role} user: {username}")
