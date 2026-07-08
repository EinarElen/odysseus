"""
chroma_client.py

Singleton ChromaDB HTTP client.
Connects to a ChromaDB instance running as a standalone service.
"""

import os
import socket
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_client = None
_server_process = None

# A short connect probe so an unreachable ChromaDB fails fast instead of
# blocking on the OS connection timeout (~30-60s, WinError 10060 on Windows),
# which otherwise stalls app startup. Tunable via CHROMADB_CONNECT_TIMEOUT.
_CONNECT_TIMEOUT = float(os.getenv("CHROMADB_CONNECT_TIMEOUT", "2.0"))
_STARTUP_TIMEOUT = float(os.getenv("CHROMADB_STARTUP_TIMEOUT", "15.0"))


def _port_open(host: str, port: int, timeout: float = None) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout or _CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def _is_local_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _truthy_env(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on", "y"}


def _chroma_executable() -> str:
    venv_bin = Path(sys.executable).resolve().parent / ("chroma.exe" if os.name == "nt" else "chroma")
    if venv_bin.exists():
        return str(venv_bin)
    return shutil.which("chroma") or ""


def _start_local_chromadb(host: str, port: int) -> bool:
    """Start a local ChromaDB server for native, non-Docker runs."""
    global _server_process

    if not _truthy_env("ODYSSEUS_CHROMADB_AUTOSTART", "true"):
        return False
    if not _is_local_host(host):
        return False
    if _server_process is not None and _server_process.poll() is None:
        return False

    chroma_bin = _chroma_executable()
    if not chroma_bin:
        logger.warning(
            "ChromaDB is not reachable and no chroma CLI was found. Native autostart "
            "requires the full `chromadb` package, not `chromadb-client`."
        )
        return False

    bind_host = "127.0.0.1" if host in {"localhost", "127.0.0.1", "::1"} else host
    data_path = Path(os.getenv("CHROMADB_PATH", "data/chroma")).resolve()
    data_path.mkdir(parents=True, exist_ok=True)
    log_path = Path(os.getenv("CHROMADB_LOG", "logs/chromadb.log")).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_file = log_path.open("ab")
    try:
        _server_process = subprocess.Popen(
            [
                chroma_bin,
                "run",
                "--host",
                bind_host,
                "--port",
                str(port),
                "--path",
                str(data_path),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
        )
    except Exception as e:
        logger.warning("Could not start local ChromaDB: %s", e)
        log_file.close()
        return False
    finally:
        if not log_file.closed:
            log_file.close()

    logger.info("Started local ChromaDB on %s:%s (pid=%s, log=%s)", bind_host, port, _server_process.pid, log_path)
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if _server_process.poll() is not None:
            logger.warning("Local ChromaDB exited during startup with code %s", _server_process.returncode)
            return False
        if _port_open(bind_host, port, timeout=0.5):
            return True
        time.sleep(0.25)
    return _port_open(bind_host, port, timeout=0.5)


def get_chroma_client():
    """Get or create the singleton ChromaDB HTTP client.

    Raises RuntimeError with a clear install hint if the `chromadb` package
    is not installed — it's an optional dependency (RAG + memory vectors).
    """
    global _client
    if _client is not None:
        return _client

    try:
        import chromadb
    except ImportError as e:
        raise RuntimeError(
            "ChromaDB integration is not installed. Install the optional "
            "dependency with: pip install chromadb-client"
        ) from e

    host = os.getenv("CHROMADB_HOST", "localhost")
    port = int(os.getenv("CHROMADB_PORT", "8100"))

    if not _port_open(host, port):
        _start_local_chromadb(host, port)

    if not _port_open(host, port):
        raise RuntimeError(
            f"ChromaDB is not reachable at {host}:{port}. Start the ChromaDB "
            f"service or set CHROMADB_HOST / CHROMADB_PORT to point at a running "
            f"instance. For native local mode, install the full `chromadb` package "
            f"and leave ODYSSEUS_CHROMADB_AUTOSTART=true."
        )

    client = chromadb.HttpClient(host=host, port=port)

    # Health check before caching — if the port is open but the service isn't
    # healthy yet (e.g. still starting), don't poison the singleton with a dead
    # client; leave _client unset so the next call retries.
    client.heartbeat()
    _client = client
    logger.info(f"ChromaDB connected: {host}:{port}")
    return _client


def reset_client():
    """Reset the singleton (e.g. after config change)."""
    global _client
    _client = None
