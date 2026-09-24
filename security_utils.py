"""SSRF guards for user/listing-supplied URLs — merged from openshorts/security_utils.py.

When code fetches URLs that someone else chose (a scraped "website" column from
Google Maps data, a URL typed into a CLI that runs in a shared/cloud context),
an attacker can point the fetcher at internal services or the cloud metadata
endpoint (169.254.169.254). ``assert_public_url`` rejects non-HTTP(S) schemes
and any host that resolves to a private / loopback / link-local / reserved
address.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    """Raised when a URL is not safe to fetch server-side."""


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def assert_public_url(url: str) -> str:
    """Return ``url`` if it is safe to fetch, else raise ``UnsafeURLError``.

    Blocks non-http(s) schemes and hosts that resolve to any non-public IP.
    Resolves every A/AAAA record so a hostname that maps to a private range
    (or to 169.254.169.254) is rejected rather than silently fetched.
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("Empty URL")

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeURLError(f"Unsupported URL scheme: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")

    # If the host is a literal IP, validate it directly (no DNS).
    try:
        ipaddress.ip_address(host)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False

    if is_ip_literal:
        if not _ip_is_public(host):
            raise UnsafeURLError(f"URL host is not a public address: {host}")
        return url

    try:
        infos = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURLError(f"Could not resolve host {host!r}: {e}")

    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise UnsafeURLError(f"Host {host!r} did not resolve")
    for ip in resolved:
        if not _ip_is_public(ip):
            raise UnsafeURLError(f"Host {host!r} resolves to a non-public address: {ip}")
    return url


def assert_safe_path(path: str, root: str) -> str:
    """Resolve a local path and ensure it remains inside an allowed root."""
    from pathlib import Path
    resolved = Path(path).expanduser().resolve()
    allowed = Path(root).expanduser().resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise UnsafeURLError("Path is outside the allowed output root")
    return str(resolved)


__all__ = ["UnsafeURLError", "assert_public_url", "assert_safe_path"]
