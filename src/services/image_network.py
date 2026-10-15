"""Resolve and pin public image destinations, including DNS handled by a Fake-IP proxy."""
import ipaddress
import socket
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit, urlunsplit
from urllib.request import getproxies, getproxies_environment, proxy_bypass, proxy_bypass_environment

from curl_cffi import CurlOpt, ffi, lib, requests

from src.errors import ComicApiError

FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_DNS_CACHE = OrderedDict()
_DNS_PENDING = {}
_DNS_LOCK = threading.Lock()


def _invalid_url():
    return ComicApiError("图片地址不被允许", 400, "invalid_request")


def _public(address):
    return address.is_global and not address.is_multicast and not address.is_unspecified


def _host_port(host, port):
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


@dataclass(frozen=True)
class ImageTarget:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]
    proxy: str | None = field(default=None, repr=False)
    attempt: int = 0

    @property
    def curl_options(self):
        address = self.addresses[self.attempt % len(self.addresses)]
        return {
            CurlOpt.CONNECT_TO: [f"{_host_port(self.host, self.port)}:{_host_port(address, self.port)}"],
            CurlOpt.PROXY: self.proxy or "",
            # Never let libcurl's environment handling change the route we validated.
            CurlOpt.NOPROXY: "" if self.proxy else "*",
            CurlOpt.HTTPPROXYTUNNEL: 1 if self.proxy and urlsplit(self.proxy).scheme in ("http", "https") else 0,
            CurlOpt.FOLLOWLOCATION: 0,
        }

    def retry(self, attempt):
        return replace(self, attempt=attempt)


@contextmanager
def image_session(target, **kwargs):
    # curl_cffi wraps RESOLVE lists but not CONNECT_TO. Own this native list
    # outside the Session: a streaming duplicate handle also borrows it until close.
    options = target.curl_options
    entries = ffi.NULL
    try:
        for entry in options[CurlOpt.CONNECT_TO]:
            updated = lib.curl_slist_append(entries, entry.encode("ascii"))
            if updated == ffi.NULL:
                raise MemoryError("Could not allocate image connection target")
            entries = updated
        options[CurlOpt.CONNECT_TO] = entries
        with requests.Session(curl_options=options, trust_env=False, **kwargs) as session:
            yield session
    finally:
        lib.curl_slist_free_all(entries)


def _parse_image_url(url):
    if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise _invalid_url()
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError()
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.port == 0 or "%" in host or not host:
            raise ValueError()
        if host == "localhost" or host.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa")):
            raise ValueError()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            try:
                # inet_aton also recognizes shortened, integer, octal and hex IPv4 forms.
                literal = ipaddress.ip_address(socket.inet_aton(host))
            except OSError:
                literal = None
        if literal is not None:
            if not _public(literal):
                raise ValueError()
            host = str(literal)
        authority = _host_port(host, port) if parsed.port is not None else (f"[{host}]" if ":" in host else host)
        normalized = urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))
        return normalized, host, port, literal
    except (ValueError, UnicodeError) as exc:
        raise _invalid_url() from exc


def _proxy_for(scheme, host, port):
    environment = getproxies_environment()
    proxies = environment or getproxies()
    authority = _host_port(host, port)
    if proxy_bypass_environment(authority, proxies) or (not environment and proxy_bypass(authority)):
        return None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        # urllib handles domain/port entries; libcurl also supports IP/CIDR entries.
        for entry in proxies.get("no", "").split(","):
            try:
                if literal in ipaddress.ip_network(entry.strip().strip("[]"), strict=False):
                    return None
            except ValueError:
                continue
    proxy = proxies.get(scheme) or proxies.get("all")
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = "http://" + proxy
    try:
        parsed = urlsplit(proxy)
        if parsed.scheme not in ("http", "https", "socks4", "socks4a", "socks5", "socks5h") or not parsed.hostname:
            raise ValueError()
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.port == 0:
            raise ValueError()
    except ValueError as exc:
        raise ComicApiError("图片请求的代理配置无效", 503, "image_proxy_config") from exc
    return proxy


def _doh_lookup(host, proxy):
    """Bootstrap HTTPS DNS with fixed public IPs; never resolve the resolver via Fake-IP DNS."""
    last_error = None
    for bootstrap in ("8.8.8.8", "8.8.4.4"):
        target = ImageTarget("https://dns.google/resolve", "dns.google", 443, (bootstrap,), proxy)
        try:
            with image_session(target) as session:
                for record_type, number in (("A", 1), ("AAAA", 28)):
                    response = session.request(
                        "GET", target.url, params={"name": host, "type": record_type},
                        headers={"Accept": "application/dns-json"}, proxy=proxy,
                        allow_redirects=False, verify=True, timeout=(5, 5))
                    if response.status_code != 200 or len(response.content) > 1024 * 1024:
                        raise ValueError("Invalid DNS response")
                    data = response.json()
                    if data.get("Status") != 0:
                        raise ValueError("DNS lookup failed")
                    addresses, ttl = [], 300
                    for answer in data.get("Answer", []):
                        ttl = min(ttl, max(0, int(answer.get("TTL", 0))))
                        if answer.get("type") != number:
                            continue
                        address = ipaddress.ip_address(answer["data"])
                        if not _public(address):
                            raise _invalid_url()
                        addresses.append(str(address))
                    if addresses:
                        return tuple(dict.fromkeys(addresses)), ttl
            raise ValueError("No public DNS records")
        except ComicApiError:
            raise
        except Exception as exc:
            last_error = exc
    raise ComicApiError("图片域名解析为 Fake-IP，且无法获取真实 DNS 结果；请确认代理可访问 dns.google", 502, "image_dns_failed") from last_error


def _real_addresses(host, proxy):
    key = host, proxy
    with _DNS_LOCK:
        cached = _DNS_CACHE.get(key)
        if cached and cached[1] > time.monotonic():
            _DNS_CACHE.move_to_end(key)
            return cached[0]
        pending = _DNS_PENDING.get(key)
        owner = pending is None
        if owner:
            pending = _DNS_PENDING[key] = Future()
    if not owner:
        return pending.result()
    try:
        addresses, ttl = _doh_lookup(host, proxy)
        with _DNS_LOCK:
            _DNS_CACHE[key] = addresses, time.monotonic() + ttl
            _DNS_CACHE.move_to_end(key)
            while len(_DNS_CACHE) > 512:
                _DNS_CACHE.popitem(last=False)
        pending.set_result(addresses)
        return addresses
    except Exception as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _DNS_LOCK:
            _DNS_PENDING.pop(key, None)


def image_target(url):
    normalized, host, port, literal = _parse_image_url(url)
    proxy = _proxy_for(urlsplit(normalized).scheme, host, port)
    if literal is not None:
        return ImageTarget(normalized, host, port, (str(literal),), proxy)
    try:
        addresses = tuple(dict.fromkeys(entry[4][0] for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        parsed = [ipaddress.ip_address(value) for value in addresses]
    except (OSError, ValueError) as exc:
        raise ComicApiError("图片地址无法解析", 502, "image_dns_failed") from exc
    if not parsed:
        raise ComicApiError("图片地址无法解析", 502, "image_dns_failed")
    if not all(_public(address) for address in parsed):
        if any(not _public(address) and not (address.version == 4 and address in FAKE_IP_NETWORK) for address in parsed):
            raise _invalid_url()
        # Fake-IP DNS means a proxy is intercepting traffic. Without an explicit proxy this is a
        # transparent/gateway proxy (e.g. Surge, Clash TUN): look up and validate the real public
        # addresses, then connect to them directly so the gateway still routes the pinned connection.
        addresses = _real_addresses(host, proxy)
    return ImageTarget(normalized, host, port, addresses, proxy)
