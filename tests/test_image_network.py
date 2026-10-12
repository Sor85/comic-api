import asyncio
import io
import socket
from types import SimpleNamespace

import pytest
from curl_cffi import CurlOpt, ffi
from PIL import Image

from src.clients.base import BaseClient
from src.services.image_proxy import ImageProxy
from src.storage import Store


@pytest.fixture(autouse=True)
def isolated_network_environment(monkeypatch):
    from src.services import image_network
    for key in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "REQUEST_METHOD"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.upper(), raising=False)
    monkeypatch.setattr(image_network, "getproxies", image_network.getproxies_environment)
    monkeypatch.setattr(image_network, "proxy_bypass", lambda host: image_network.proxy_bypass_environment(host))
    with image_network._DNS_LOCK:
        image_network._DNS_CACHE.clear()
        image_network._DNS_PENDING.clear()


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:18888")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.9", 443))])
    content = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(content, "PNG")
    state = SimpleNamespace(calls=[], sessions=[], picture=content.getvalue(), addresses=["93.184.216.34"], status=200, dns_status=0)
    state.responses = {}
    state.addresses_by_host = {}

    class Session:
        def __init__(self, **kwargs):
            self.options = dict(kwargs.get("curl_options", {}))
            if CurlOpt.CONNECT_TO in self.options:
                pointer = self.options[CurlOpt.CONNECT_TO]
                values = []
                while pointer != ffi.NULL:
                    values.append(ffi.string(pointer.data).decode())
                    pointer = pointer.next
                self.options[CurlOpt.CONNECT_TO] = values
            self.cookies = kwargs.get("cookies", {})
            self.closed = False
            state.sessions.append(self)

        def request(self, method, url, **kwargs):
            state.calls.append((url, kwargs, self.options))
            if url.startswith("https://dns.google/"):
                addresses = state.addresses_by_host.get(kwargs["params"]["name"], state.addresses)
                body = {"Status": state.dns_status, "Answer": [{"type": 1, "data": value, "TTL": 60} for value in addresses]}
                return SimpleNamespace(status_code=200, json=lambda: body, content=b"{}")
            status, headers = state.responses.get(url, (state.status, {}))
            return SimpleNamespace(status_code=status, headers=headers, content=state.picture,
                                   iter_content=lambda: iter([state.picture]), close=lambda: None)

        def close(self):
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr("src.clients.base.requests.Session", Session)
    return state


def test_cover_through_fake_dns_uses_validated_public_connection(network, tmp_path):
    client = BaseClient()
    plugin = SimpleNamespace(client=client, transform_image=lambda data, *args: data)
    result = asyncio.run(ImageProxy(Store(tmp_path)).image(plugin, "jm", "https://cdn.example/cover.jpg"))
    assert result == network.picture
    url, kwargs, options = network.calls[-1]
    assert url == "https://cdn.example/cover.jpg"
    assert options[CurlOpt.CONNECT_TO] == ["cdn.example:443:93.184.216.34:443"]
    assert options[CurlOpt.PROXY] == "http://127.0.0.1:18888"
    assert options[CurlOpt.NOPROXY] == ""
    assert kwargs["allow_redirects"] is False
    assert kwargs["verify"] is True
    assert network.sessions[-1].closed and not network.sessions[0].closed


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://127.1/", "http://2130706433/", "http://0x7f000001/",
    "http://0177.0.0.1/", "http://0.0.0.0/", "http://10.0.0.1/", "http://172.16.0.1/",
    "http://192.168.1.1/", "http://169.254.169.254/", "http://198.18.1.2/", "http://224.0.0.1/",
    "http://[::1]/", "http://[fc00::1]/", "http://[::ffff:127.0.0.1]/", "http://[fe80::1%25en0]/",
    "http://localhost/", "http://router.local/", "http://host.lan/", "http://host.internal/",
    "file:///etc/passwd", "ftp://cdn.example/a.jpg", "gopher://cdn.example/", "http://cdn.example:0/",
    "https://user:password@cdn.example/image", "https://@cdn.example/image", "https://%31%32%37.0.0.1/",
    "https://cdn.example\\@127.0.0.1/", "https://cdn.example/\nheader", "https://cdn.example/#fragment",
])
def test_private_and_ambiguous_urls_stay_blocked(network, url):
    from src.errors import ComicApiError
    from src.services.image_network import image_target
    with pytest.raises(ComicApiError) as error:
        image_target(url)
    assert error.value.code == "invalid_request"
    assert not network.calls


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["192.168.1.1"], ["198.18.1.1", "169.254.169.254"], ["93.184.216.34", "::1"]])
def test_private_dns_answers_cannot_use_fake_ip_fallback(network, monkeypatch, addresses):
    from src.errors import ComicApiError
    from src.services.image_network import image_target
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 443)) for value in addresses])
    with pytest.raises(ComicApiError) as error:
        image_target("https://cdn.example/a.jpg")
    assert error.value.code == "invalid_request" and not network.calls


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["169.254.169.254"], ["198.18.2.2"], ["224.0.0.1"], ["93.184.216.34", "10.0.0.1"]])
def test_untrusted_real_dns_answers_are_rejected(network, addresses):
    from src.errors import ComicApiError
    from src.services.image_network import image_target
    network.addresses = addresses
    with pytest.raises(ComicApiError) as error:
        image_target("https://cdn.example/a.jpg")
    assert error.value.code == "invalid_request"
    assert all(url.startswith("https://dns.google/") for url, _, _ in network.calls)


@pytest.mark.parametrize("bypass", [None, "*", "cdn.example", ".example", "cdn.example:443"])
def test_fake_ip_requires_proxy_and_respects_no_proxy(network, monkeypatch, bypass):
    from src.errors import ComicApiError
    from src.services.image_network import image_target
    if bypass is None:
        monkeypatch.delenv("https_proxy")
    else:
        monkeypatch.setenv("no_proxy", bypass)
    with pytest.raises(ComicApiError) as error:
        image_target("https://cdn.example/a.jpg")
    assert error.value.code == "image_proxy_required" and not network.calls


def test_system_proxy_is_used_without_environment_proxy(network, monkeypatch):
    from src.services import image_network
    monkeypatch.delenv("https_proxy")
    monkeypatch.setattr(image_network, "getproxies", lambda: {"https": "http://127.0.0.1:6152"})
    monkeypatch.setattr(image_network, "proxy_bypass", lambda host: False)
    target = image_network.image_target("https://cdn.example/a.jpg")
    assert target.proxy == "http://127.0.0.1:6152" and target.addresses == ("93.184.216.34",)


def test_system_proxy_exceptions_remain_effective(network, monkeypatch):
    from src.errors import ComicApiError
    from src.services import image_network
    monkeypatch.delenv("https_proxy")
    monkeypatch.setattr(image_network, "getproxies", lambda: {"https": "http://127.0.0.1:6152"})
    monkeypatch.setattr(image_network, "proxy_bypass", lambda host: True)
    with pytest.raises(ComicApiError) as error:
        image_network.image_target("https://cdn.example/a.jpg")
    assert error.value.code == "image_proxy_required" and not network.calls


def test_dns_failure_never_falls_back_to_unchecked_connection(network):
    from src.errors import ComicApiError
    from src.services.image_network import image_target
    network.dns_status = 2
    with pytest.raises(ComicApiError) as error:
        image_target("https://cdn.example/a.jpg")
    assert error.value.code == "image_dns_failed"
    assert all(url.startswith("https://dns.google/") for url, _, _ in network.calls)


def test_public_resolution_is_pinned_and_no_proxy_is_preserved(network, monkeypatch):
    from src.services.image_proxy import download_image
    monkeypatch.setenv("no_proxy", "cdn.example")
    calls = []
    def resolve(*args, **kwargs):
        calls.append(args)
        address = "93.184.216.34" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert download_image(BaseClient(), "https://cdn.example/a.jpg") == network.picture
    assert len(calls) == 1
    _, kwargs, options = network.calls[-1]
    assert options[CurlOpt.CONNECT_TO] == ["cdn.example:443:93.184.216.34:443"]
    assert options[CurlOpt.PROXY] == "" and options[CurlOpt.NOPROXY] == "*"
    assert kwargs["proxy"] == ""


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_image_redirects_validate_and_pin_each_destination(network, tmp_path, status):
    original = "https://storage-b.picacomic.com/static/cover.jpg"
    destination = "https://img.picacomic.com/static/cover.jpg"
    network.responses[original] = status, {"Location": destination}
    network.addresses_by_host["img.picacomic.com"] = ["1.1.1.1"]
    client = BaseClient()
    transformed = []
    def transform(data, chapter_id, url):
        transformed.append((chapter_id, url))
        return data
    plugin = SimpleNamespace(client=client, transform_image=transform)
    proxy = ImageProxy(Store(tmp_path))
    assert asyncio.run(proxy.image(plugin, "bika", original, "1")) == network.picture
    image_calls = [call for call in network.calls if not call[0].startswith("https://dns.google/")]
    assert [call[0] for call in image_calls] == [original, destination]
    for (url, kwargs, options), host, address in zip(image_calls,
            ("storage-b.picacomic.com", "img.picacomic.com"), (network.addresses[0], "1.1.1.1")):
        assert options[CurlOpt.CONNECT_TO] == [f"{host}:443:{address}:443"]
        assert options[CurlOpt.FOLLOWLOCATION] == 0
        assert kwargs["allow_redirects"] is False and kwargs["verify"] is True
        assert kwargs["headers"]["Referer"] == f"https://{host}/"
        assert {key.lower() for key in kwargs["headers"]} == {"referer", "user-agent"}
    assert all(session.closed for session in network.sessions[1:])
    assert transformed == [("1", original)]
    count = len(network.calls)
    assert asyncio.run(proxy.image(plugin, "bika", original, "1")) == network.picture
    assert len(network.calls) == count and transformed == [("1", original)]


@pytest.mark.parametrize("location, destination", [
    ("/new/cover.jpg", "https://cdn.example/new/cover.jpg"),
    ("../cover.jpg", "https://cdn.example/cover.jpg"),
    ("?version=2", "https://cdn.example/static/cover.jpg?version=2"),
    ("//other.example/cover.jpg", "https://other.example/cover.jpg"),
])
def test_image_redirects_resolve_relative_locations(network, location, destination):
    from src.services.image_proxy import download_image
    original = "https://cdn.example/static/cover.jpg"
    network.responses[original] = 302, {"Location": location}
    assert download_image(BaseClient(), original, retries=1) == network.picture
    assert network.calls[-1][0] == destination


@pytest.mark.parametrize("location", [
    "http://127.0.0.1/private", "//169.254.169.254/metadata", "https://198.18.1.2/private",
    "http://[::1]/private", "http://router.local/private", "file:///etc/passwd",
    "https://user:password@cdn.example/image", "https://cdn.example/#fragment",
])
def test_image_redirects_cannot_reach_private_or_invalid_urls(network, location):
    from src.errors import ComicApiError
    from src.services.image_proxy import download_image
    original = "https://cdn.example/cover.jpg"
    network.responses[original] = 301, {"Location": location}
    with pytest.raises(ComicApiError) as error:
        download_image(BaseClient(), original, retries=1)
    assert error.value.code == "invalid_request"
    assert [url for url, _, _ in network.calls if not url.startswith("https://dns.google/")] == [original]


@pytest.mark.parametrize("private_dns", [False, True])
def test_image_redirects_reject_private_dns_at_later_hop(network, monkeypatch, private_dns):
    from src.errors import ComicApiError
    from src.services.image_proxy import download_image
    original = "https://cdn.example/cover.jpg"
    middle = "https://second.example/cover.jpg"
    destination = "https://third.example/cover.jpg"
    network.responses.update({original: (302, {"Location": middle}), middle: (307, {"Location": destination})})
    if private_dns:
        network.addresses_by_host["third.example"] = ["127.0.0.1"]
    else:
        def resolve(host, *args, **kwargs):
            address = "127.0.0.1" if host == "third.example" else "198.18.0.9"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
        monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(ComicApiError) as error:
        download_image(BaseClient(), original, retries=1)
    assert error.value.code == "invalid_request"
    assert [url for url, _, _ in network.calls if not url.startswith("https://dns.google/")] == [original, middle]


@pytest.mark.parametrize("steps", [5, 6])
def test_image_redirect_limit(network, steps):
    from src.errors import ComicApiError
    from src.services.image_proxy import download_image
    urls = [f"https://cdn.example/{index}.jpg" for index in range(steps + 1)]
    for current, following in zip(urls, urls[1:]):
        network.responses[current] = 301, {"Location": following}
    if steps == 5:
        assert download_image(BaseClient(), urls[0], retries=1) == network.picture
    else:
        with pytest.raises(ComicApiError, match="重定向次数过多") as error:
            download_image(BaseClient(), urls[0], retries=1)
        assert error.value.code == "image_download_failed"
    assert [url for url, _, _ in network.calls if not url.startswith("https://dns.google/")] == urls[:6]


def test_image_redirect_loop_is_rejected(network):
    from src.errors import ComicApiError
    from src.services.image_proxy import download_image
    original, destination = "https://cdn.example/a.jpg", "https://cdn.example/b.jpg"
    network.responses.update({original: (302, {"Location": destination}), destination: (302, {"Location": original})})
    with pytest.raises(ComicApiError, match="重定向存在循环") as error:
        download_image(BaseClient(), original, retries=1)
    assert error.value.code == "image_download_failed"
    assert [url for url, _, _ in network.calls if not url.startswith("https://dns.google/")] == [original, destination]


def test_image_redirect_revalidates_same_host_against_dns_rebinding(network, monkeypatch):
    from src.errors import ComicApiError
    from src.services.image_proxy import download_image
    original = "https://cdn.example/a.jpg"
    network.responses[original] = 301, {"Location": "/b.jpg"}
    resolutions = []
    def resolve(*args, **kwargs):
        address = network.addresses[0] if not resolutions else "127.0.0.1"
        resolutions.append(address)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(ComicApiError) as error:
        download_image(BaseClient(), original, retries=1)
    assert error.value.code == "invalid_request" and len(resolutions) == 2
    assert [url for url, _, _ in network.calls] == [original]


def test_image_redirect_respects_destination_no_proxy(network, monkeypatch):
    from src.errors import ComicApiError
    from src.services.image_proxy import download_image
    original = "https://cdn.example/a.jpg"
    network.responses[original] = 302, {"Location": "https://other.example/b.jpg"}
    monkeypatch.setenv("no_proxy", "other.example")
    with pytest.raises(ComicApiError) as error:
        download_image(BaseClient(), original, retries=1)
    assert error.value.code == "image_proxy_required"
    assert [url for url, _, _ in network.calls if not url.startswith("https://dns.google/")] == [original]


@pytest.mark.parametrize("headers", [{}, {"Location": ""}])
def test_redirect_without_location_is_rejected(network, headers):
    from src.errors import ComicApiError
    from src.services.image_proxy import download_image
    network.responses["https://cdn.example/a.jpg"] = 302, headers
    with pytest.raises(ComicApiError, match="重定向缺少目标地址") as error:
        download_image(BaseClient(), "https://cdn.example/a.jpg", retries=1)
    assert error.value.code == "image_download_failed"
    image_calls = [call for call in network.calls if not call[0].startswith("https://dns.google/")]
    assert len(image_calls) == 1
    assert image_calls[0][1]["allow_redirects"] is False
    assert image_calls[0][2][CurlOpt.FOLLOWLOCATION] == 0


@pytest.mark.parametrize("location", ["http://[", "https://[invalid]/image"])
def test_malformed_redirect_location_has_explicit_error(network, location):
    from src.errors import ComicApiError
    from src.services.image_proxy import download_image
    original = "https://cdn.example/a.jpg"
    network.responses[original] = 302, {"Location": location}
    with pytest.raises(ComicApiError, match="重定向地址无效") as error:
        download_image(BaseClient(), original, retries=1)
    assert error.value.code == "image_download_failed"
    assert [url for url, _, _ in network.calls if not url.startswith("https://dns.google/")] == [original]


def test_redirect_target_retries_rotate_its_own_pinned_addresses(network, monkeypatch):
    from src.errors import ComicApiError
    from src.services import image_proxy
    original, destination = "https://cdn.example/a.jpg", "https://other.example/a.jpg"
    network.responses[original] = 301, {"Location": destination}
    network.addresses_by_host["other.example"] = [network.addresses[0], "1.1.1.1"]
    client = BaseClient()
    send = client.request
    attempts, sleeps = [], []
    def request(method, url, **kwargs):
        if url == destination:
            attempts.append(kwargs["_image_target"].attempt)
            if len(attempts) == 1:
                raise ComicApiError("无法连接图源", 502, "upstream_network")
        return send(method, url, **kwargs)
    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(image_proxy.time, "sleep", sleeps.append)
    assert image_proxy.download_image(client, original) == network.picture
    assert attempts == [0, 1] and sleeps == [0.5]
    image_calls = [call for call in network.calls if not call[0].startswith("https://dns.google/")]
    assert [call[0] for call in image_calls] == [original, destination]
    assert image_calls[-1][2][CurlOpt.CONNECT_TO] == ["other.example:443:1.1.1.1:443"]


def test_local_sync_stream_uses_same_pinned_policy(network):
    import threading
    from src.services.local_images import receive_image
    client = BaseClient()
    plugin = SimpleNamespace(client=client, transform_image=lambda data, *args: data)
    config = {"retries": 0, "speed_limit_kbps": 0, "image_format": "original", "quality": 90}
    data, extension, received = receive_image(plugin, "https://cdn.example/1.png", "1", config, threading.Event())
    assert data == network.picture and extension == "png" and received == len(data)
    _, kwargs, options = network.calls[-1]
    assert kwargs["stream"] and options[CurlOpt.CONNECT_TO] == ["cdn.example:443:93.184.216.34:443"]
    assert network.sessions[-1].closed


def test_dns_lookup_is_cached_coalesced_and_can_expire(network, monkeypatch):
    import concurrent.futures
    import threading
    from src.services import image_network
    entered, release = threading.Event(), threading.Event()
    calls = []
    def lookup(host, proxy):
        calls.append(host)
        entered.set()
        assert release.wait(2)
        return ("93.184.216.34",), 60
    monkeypatch.setattr(image_network, "_doh_lookup", lookup)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(image_network.image_target, "https://cdn.example/a.jpg") for _ in range(8)]
        assert entered.wait(1)
        release.set()
        assert all(job.result().addresses == ("93.184.216.34",) for job in jobs)
    assert len(calls) == 1
    image_network.image_target("https://cdn.example/another.jpg")
    assert len(calls) == 1
    key = "cdn.example", "http://127.0.0.1:18888"
    image_network._DNS_CACHE[key] = (("93.184.216.34",), 0)
    image_network.image_target("https://cdn.example/a.jpg")
    assert len(calls) == 2


def test_transport_pins_do_not_leak_into_api_session(network):
    from src.services.image_proxy import download_image
    client = BaseClient()
    download_image(client, "https://first.example/a.jpg")
    download_image(client, "https://second.example/b.jpg")
    client.request("GET", "https://api.example/metadata")
    image_calls = [call for call in network.calls if call[0].startswith(("https://first", "https://second"))]
    assert image_calls[0][2][CurlOpt.CONNECT_TO] == ["first.example:443:93.184.216.34:443"]
    assert image_calls[1][2][CurlOpt.CONNECT_TO] == ["second.example:443:93.184.216.34:443"]
    assert network.calls[-1][2] == {}


def test_dns_bootstrap_is_pinned_and_gets_no_account_cookies(network):
    from src.services.image_proxy import download_image
    client = BaseClient()
    client.session.cookies = {"private-session": "test-only"}
    download_image(client, "https://cdn.example/a.jpg")
    url, kwargs, options = network.calls[0]
    assert url == "https://dns.google/resolve"
    assert options[CurlOpt.CONNECT_TO] == ["dns.google:443:8.8.8.8:443"]
    assert network.sessions[1].cookies == {}
    assert kwargs["verify"] is True and kwargs["allow_redirects"] is False


@pytest.mark.parametrize("url,bypass", [
    ("https://93.184.216.34/image", "93.184.216.0/24"),
    ("https://[2606:4700:4700::1111]/image", "2606:4700::/32"),
    ("https://[2606:4700:4700::1111]/image", "2606:4700:4700::1111"),
])
def test_public_literal_no_proxy_ip_and_cidr(network, monkeypatch, url, bypass):
    from src.services.image_network import image_target
    monkeypatch.setenv("NO_PROXY", bypass)
    target = image_target(url)
    assert target.proxy is None and target.curl_options[CurlOpt.PROXY] == ""
    assert not network.calls


def test_native_connection_list_lives_until_stream_and_session_close(network, monkeypatch):
    from src.services import image_network
    original = image_network.lib
    frees = []
    def free(pointer):
        assert network.sessions[-1].closed
        frees.append(True)
        original.curl_slist_free_all(pointer)
    monkeypatch.setattr(image_network, "lib", SimpleNamespace(curl_slist_append=original.curl_slist_append, curl_slist_free_all=free))
    client = BaseClient()
    target = image_network.ImageTarget("https://cdn.example/a.jpg", "cdn.example", 443, ("93.184.216.34",))
    with pytest.raises(RuntimeError):
        with client.stream("GET", target.url, _image_target=target) as response:
            assert not frees and not network.sessions[-1].closed
            assert next(response.iter_content()) == network.picture
            raise RuntimeError("consumer cancelled")
    assert frees == [True]
    assert client._request_slots.acquire(blocking=False)
    client._request_slots.release()


def test_actual_http_proxy_connect_is_pinned_not_hostname(monkeypatch):
    import socketserver
    import threading
    from src.services import image_network
    from src.services.image_proxy import download_image
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, "PNG")
    picture = output.getvalue()
    seen = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(5)
            connect = self.rfile.readline().decode().strip()
            while self.rfile.readline().strip():
                pass
            self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self.wfile.flush()
            request = self.rfile.readline().decode().strip()
            headers = []
            while line := self.rfile.readline().strip():
                headers.append(line.decode())
            seen.append((connect, request, headers))
            self.wfile.write(f"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: {len(picture)}\r\nConnection: close\r\n\r\n".encode() + picture)
            self.wfile.flush()

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.1", 80))])
    monkeypatch.setattr(image_network, "_doh_lookup", lambda *args: (("93.184.216.34",), 60))
    client = BaseClient()
    try:
        assert download_image(client, "http://cdn.example/a.png", retries=1) == picture
        assert seen[0][0] == "CONNECT 93.184.216.34:80 HTTP/1.1"
        assert seen[0][1] == "GET /a.png HTTP/1.1"
        assert "Host: cdn.example" in seen[0][2]
    finally:
        client.session.close()
        server.shutdown()
        server.server_close()
        worker.join(2)
