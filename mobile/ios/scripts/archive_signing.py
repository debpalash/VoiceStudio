#!/usr/bin/env python3
"""Reuse one development identity for archives; distribution stays cloud-managed."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys


def command(args, **kwargs):
    try:
        result = subprocess.run(args, capture_output=True, timeout=300, **kwargs)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Signing operation timed out: {Path(args[0]).name} {args[1]}") from None
    if result.returncode:
        # Never include command arguments, certificate subjects or keychain output:
        # security import and partition-list commands contain ephemeral passwords.
        raise RuntimeError(f"Signing operation failed: {Path(args[0]).name} {args[1]}")
    return result.stdout


def certificate_identity(path, password, team):
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise ValueError("APPLE_TEAM_ID must be the 10-character developer team ID")
    env = {**os.environ, "IOS_ARCHIVE_CERTIFICATE_PASSWORD": password}
    certificate = command(["openssl", "pkcs12", "-in", str(path), "-clcerts", "-nokeys",
                           "-passin", "env:IOS_ARCHIVE_CERTIFICATE_PASSWORD"], env=env)
    if certificate.count(b"-----BEGIN CERTIFICATE-----") != 1:
        raise ValueError("Archive certificate must contain exactly one signing identity")
    subject = command(["openssl", "x509", "-noout", "-subject", "-nameopt", "multiline"], input=certificate).decode()
    if not re.search(r"^\s*organizationalUnitName\s*=\s*" + re.escape(team) + r"\s*$", subject, re.M):
        raise ValueError("Archive certificate belongs to a different Apple team")
    if not re.search(r"^\s*commonName\s*=\s*Apple Development:", subject, re.M):
        raise ValueError("Use an Apple Development certificate for the archive")
    command(["openssl", "x509", "-noout", "-checkend", "0"], input=certificate)
    fingerprint = command(["openssl", "x509", "-noout", "-fingerprint", "-sha1"], input=certificate).decode()
    match = re.search(r"=([0-9A-Fa-f:]+)\s*$", fingerprint)
    if not match or len(match[1].replace(":", "")) != 40:
        raise ValueError("Could not identify the archive certificate")
    return match[1].replace(":", "").upper()


def paths():
    directory = Path(os.environ["RUNNER_TEMP"])
    return directory / "voicestudio-ios-signing.keychain-db", directory / "voicestudio-ios-keychains.json"


def cleanup():
    keychain, search_list = paths()
    try:
        if search_list.is_file():
            previous = json.loads(search_list.read_text())
            command(["security", "list-keychains", "-d", "user", "-s", *previous])
            search_list.unlink()
    finally:
        try:
            keychain.with_suffix(".p12").unlink(missing_ok=True)
        finally:
            if keychain.exists():
                command(["security", "delete-keychain", str(keychain)])


def install():
    encoded = os.environ.get("IOS_ARCHIVE_CERTIFICATE", "")
    password = os.environ.get("IOS_ARCHIVE_CERTIFICATE_PASSWORD", "")
    if not encoded or not password:
        raise ValueError("Set IOS_ARCHIVE_CERTIFICATE and IOS_ARCHIVE_CERTIFICATE_PASSWORD in this signing environment")
    os.umask(0o077)
    keychain, search_list = paths()
    certificate_path = keychain.with_suffix(".p12")
    if keychain.exists() or search_list.exists() or certificate_path.exists():
        raise ValueError("Archive signing temporary files already exist")
    created = False
    try:
        certificate_path.write_bytes(base64.b64decode("".join(encoded.split()), validate=True))
        identity = certificate_identity(certificate_path, password, os.environ.get("TEAM_ID", ""))
        previous = shlex.split(command(["security", "list-keychains", "-d", "user"]).decode())
        keychain_password = secrets.token_urlsafe(48)
        command(["security", "create-keychain", "-p", keychain_password, str(keychain)])
        created = True
        search_list.write_text(json.dumps(previous))
        command(["security", "set-keychain-settings", "-lut", "3600", str(keychain)])
        command(["security", "unlock-keychain", "-p", keychain_password, str(keychain)])
        command(["security", "import", str(certificate_path), "-P", password, "-k", str(keychain),
                 "-T", "/usr/bin/codesign", "-T", "/usr/bin/security"])
        command(["security", "set-key-partition-list", "-S", "apple-tool:,apple:", "-s",
                 "-k", keychain_password, str(keychain)])
        available = command(["security", "find-identity", "-v", "-p", "codesigning", str(keychain)]).decode()
        if identity not in available:
            raise ValueError("The archive certificate has no valid matching private key")
        command(["security", "list-keychains", "-d", "user", "-s", str(keychain), *previous])
        print("Existing archive signing identity installed.")
    except BaseException:
        if created:
            cleanup()
        raise
    finally:
        certificate_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        {"install": install, "cleanup": cleanup}[sys.argv[1]]()
    except Exception as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
