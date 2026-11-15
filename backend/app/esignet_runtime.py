"""Keep an already-configured local WSL identity provider available with the app."""
import logging
import os
import shutil
import subprocess
from urllib.parse import urlsplit

from . import config


def start_local_runtime():
    # Docker Desktop and deployed providers manage their own lifetime. The WSL
    # setup marker identifies installations that need a foreground WSL session.
    runtime = config.ROOT_DIR / 'var' / 'esignet'
    if (os.name != 'nt' or not config.ESIGNET_DEMO_ENABLED
            or urlsplit(config.ESIGNET_ISSUER).hostname not in ('127.0.0.1', 'localhost', '::1')
            or not (runtime / 'wsl-keeper.pid').is_file()
            or not (runtime / 'runtime.env').is_file()):
        return None
    executable = shutil.which('wsl')
    if not executable:
        return None
    try:
        return subprocess.Popen([executable, '-d', 'Ubuntu', '--', 'sleep', 'infinity'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        logging.getLogger(__name__).exception('Could not keep the local identity provider running')
        return None


def stop_local_runtime(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
