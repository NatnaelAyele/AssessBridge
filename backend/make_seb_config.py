"""Build the local generic SEB configuration (exam-specific configs are stored in MySQL)."""

import argparse
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

from app.seb_config import build_encrypted_configuration, seb_json


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "backend" / "seb" / "AssessBridge.seb"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path:
        parser.error("Use an origin such as http://127.0.0.1:8000 or https://exam.example.org")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        parser.error("A non-local SEB address must use HTTPS")

    existing = dotenv_values(ROOT / ".env")
    config_password = existing.get("SEB_CONFIG_PASSWORD") or secrets.token_urlsafe(18)
    quit_password = existing.get("SEB_QUIT_PASSWORD") or secrets.token_urlsafe(18)
    key, data = build_encrypted_configuration(
        base_url, base_url + "/exam-entry", config_password, quit_password,
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(data)
    env = ROOT / ".env"
    for name, value in {
        "SEB_REQUIRED": "true",
        "SEB_BASE_URL": base_url,
        "SEB_CONFIG_KEY": key,
        "SEB_CONFIG_PASSWORD": config_password,
        "SEB_QUIT_PASSWORD": quit_password,
    }.items():
        set_key(env, name, value)
    print(f"Created {OUTPUT}")
    print("SEB credentials were saved to .env. Restart Uvicorn to load the new key.")


if __name__ == "__main__":
    main()
