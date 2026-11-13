"""Build password-encrypted SEB configurations and their Config Keys."""

import gzip
import base64
import hashlib
import hmac
import json
import plistlib
import re
import secrets
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from .config import JWT_SECRET, SEB_CONFIG_PASSWORD


SHORT_EXAM_USERNAME = re.compile(r"[A-HJ-NP-Z2-9]{5}\Z")


def _short_code(purpose: str, exam_username: str) -> str:
    digest = hmac.new(JWT_SECRET.encode(), f"{purpose}:{exam_username}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:6]).decode("ascii")


def config_password_for(exam_username: str) -> str:
    """Keep old encrypted SEB files compatible with their original password."""
    return _short_code("seb-config", exam_username) if SHORT_EXAM_USERNAME.fullmatch(exam_username) else SEB_CONFIG_PASSWORD


def quit_password_for(exam_username: str) -> str:
    """Stable, invitation-specific emergency code for the native SEB quit dialog."""
    if SHORT_EXAM_USERNAME.fullmatch(exam_username):
        return _short_code("seb-quit", exam_username)
    digest = hmac.new(JWT_SECRET.encode(), ("seb-quit:" + exam_username).encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:18]).decode().rstrip("=")


def seb_json(value):
    if isinstance(value, dict):
        return {key: seb_json(value[key]) for key in sorted(value, key=str.casefold)}
    if isinstance(value, list):
        return [seb_json(item) for item in value]
    return value


def encrypt_rncryptor_v3(plain: bytes, password: str) -> bytes:
    encryption_salt, hmac_salt, iv = secrets.token_bytes(8), secrets.token_bytes(8), secrets.token_bytes(16)
    password_bytes = password.encode("utf-8")
    encryption_key = hashlib.pbkdf2_hmac("sha1", password_bytes, encryption_salt, 10000, 32)
    hmac_key = hashlib.pbkdf2_hmac("sha1", password_bytes, hmac_salt, 10000, 32)
    padder = PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    body = bytes((3, 1)) + encryption_salt + hmac_salt + iv + ciphertext
    return body + hmac.new(hmac_key, body, hashlib.sha256).digest()


def build_settings(base_url: str, start_url: str, quit_password: str) -> dict:
    parsed = urlsplit(base_url)
    return {
        "sebConfigPurpose": 0,
        "startURL": start_url,
        "quitURL": base_url + "/seb/exit",
        "quitURLConfirm": True,
        "quitURLRestart": False,
        "allowQuit": True,
        "hashedQuitPassword": hashlib.sha256(quit_password.encode()).hexdigest(),
        "allowSwitchToApplications": False,
        "allowUserSwitching": False,
        "allowScreenSharing": False,
        "allowVirtualMachine": False,
        "allowVideoCapture": True,
        "allowAudioCapture": True,
        "browserMediaCaptureCamera": True,
        "browserMediaCaptureMicrophone": True,
        "enableAltTab": False,
        "enablePrintScreen": False,
        "enableBrowserWindowToolbar": False,
        "allowBrowsingBackForward": False,
        "monitorProcesses": True,
        "browserWindowWebView": 3,
        "sendBrowserExamKey": False,
        "URLFilterEnable": True,
        "URLFilterEnableContentFilter": False,
        "URLFilterRules": [{"active": True, "action": 1, "regex": False, "expression": parsed.netloc + "/*"}],
        "removeBrowserProfile": True,
        "removeLocalStorage": True,
        "restartExamUseStartURL": False,
        "restartExamPasswordProtected": True,
    }


def build_encrypted_configuration(base_url: str, start_url: str, config_password: str, quit_password: str) -> tuple[str, bytes]:
    settings = build_settings(base_url, start_url, quit_password)
    key = hashlib.sha256(json.dumps(seb_json(settings), separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    xml = plistlib.dumps(settings, fmt=plistlib.FMT_XML, sort_keys=True)
    data = gzip.compress(b"pswd" + encrypt_rncryptor_v3(gzip.compress(xml), config_password))
    return key, data
