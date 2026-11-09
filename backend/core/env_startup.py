"""Load optional startup environment files without letting bad encoding stop the app."""
import logging
import os

logger = logging.getLogger("omnivoice.env_startup")


def _load_project_env(dotenv, path):
    if not path or not os.path.isfile(path):
        return
    try:
        # Windows editors commonly save dotenv files as UTF-16 with a BOM.
        with open(path, "rb") as source:
            prefix = source.read(4)
        if prefix.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            encoding = "utf-32"
        elif prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        else:
            encoding = "utf-8-sig"
        dotenv.load_dotenv(path, override=False, encoding=encoding)
    except (UnicodeError, OSError):
        logger.warning("Cannot load environment file %s: check its encoding and file permissions", path)


def load_startup_env(discovered, project):
    try:
        import dotenv
    except ImportError:
        return
    _load_project_env(dotenv, discovered)
    if project != discovered:
        _load_project_env(dotenv, project)
    # Explicit saved settings override launcher defaults, independently of .env.
    from core.user_env import load_into_environ
    try:
        load_into_environ()
    except (UnicodeError, OSError):
        logger.warning("Cannot load saved environment settings: check their encoding and file permissions")
