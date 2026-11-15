"""Postgres for database tests.

TEST_DATABASE_URL wins. Otherwise a temporary local cluster is started.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest


def _postgres_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    matches = sorted(Path("/usr/lib/postgresql").glob(f"*/bin/{name}"))
    if matches:
        return str(matches[-1])
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def postgres_url(tmp_path_factory):
    configured = os.getenv("TEST_DATABASE_URL")
    if configured:
        yield configured
        return

    initdb = _postgres_bin("initdb")
    pg_ctl = _postgres_bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("PostgreSQL tools are not installed and TEST_DATABASE_URL is unset")

    data = tmp_path_factory.mktemp("pgdata")
    socket_dir = tmp_path_factory.mktemp("pgsock")
    port = _free_port()
    subprocess.run(
        [initdb, "-D", str(data), "-U", "postgres", "--auth=trust", "--no-sync"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-w",
            "-o",
            f"-p {port} -k {socket_dir} -c listen_addresses=",
            "start",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"host={socket_dir} port={port} user=postgres dbname=postgres"
    try:
        yield url
    finally:
        subprocess.run([pg_ctl, "-D", str(data), "-w", "-m", "fast", "stop"], check=False)
