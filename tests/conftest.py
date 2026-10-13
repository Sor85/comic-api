import socket

import pytest


@pytest.fixture(autouse=True)
def site_auth_environment(monkeypatch):
    """Keep tests independent of deployment credentials and the local .env file."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_USERNAME", "")
    monkeypatch.setenv("AUTH_PASSWORD", "")


@pytest.fixture(autouse=True)
def synthetic_source_dns(monkeypatch):
    """Existing fake sources must stay offline when image downloads validate DNS."""
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host in ("demo.invalid", "images.invalid"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
