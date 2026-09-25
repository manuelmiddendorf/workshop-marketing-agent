import json
import socket
from pathlib import Path

import pytest


@pytest.fixture
def complete_input():
    path = Path(__file__).resolve().parents[1] / "examples/workshops/synthetic-complete-input.json"
    return json.loads(path.read_text())


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def unexpected_network(*args, **kwargs):
        pytest.fail("Workshop validation must not access the network")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    monkeypatch.setattr(socket.socket, "connect", unexpected_network)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected_network)
