"""
Outbound connections to an address somebody typed into the panel.

A few of the server's connections go wherever the panel points them: the
proxy list import, a proxy itself, the Telegram relay and proxy, the SMTP
host. Each accepts public addresses only — every address a name resolves
to, not just the first, since a name can answer with one public and one
internal address. Loopback, private, link-local (the cloud metadata address
among them), shared carrier-grade NAT, reserved, multicast and unspecified
addresses are refused, and so is an IPv6 address that only wraps one of them.

fetch_text() connects to the address it checked, not to the name again, so
a name that answers differently a moment later changes nothing, and follows
redirects by hand, checking every hop the same way.

The cluster's own egress policy says the same thing one layer down; this
stands on its own without it.
"""
import asyncio
import ipaddress
import socket
from typing import List, Tuple
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

RESOLVE_TIMEOUT = 5.0
MAX_REDIRECTS = 3

REFUSED = "نشانی‌های داخلی و خصوصی پذیرفته نمی‌شوند؛ فقط نشانی عمومی اینترنت"


class BlockedAddress(ValueError):
    """Not a public address, or not a URL this may fetch. The message is
    Persian, for the panel."""


def is_ip(host: str) -> bool:
    """A literal address rather than a name."""
    try:
        ipaddress.ip_address((host or "").strip().strip("[]").split("%", 1)[0])
        return True
    except ValueError:
        return False


def is_public(ip: str) -> bool:
    """A globally routable unicast address. is_global alone is not enough:
    it calls 224.0.0.1 and ff02::1 global, and on the image's Python 3.10
    2002:7f00:1:: too — which is 127.0.0.1, wrapped."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])      # an IPv6 zone is not the address
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        # ::ffff:a.b.c.d and 2002:aabb:ccdd:: are the IPv4 address they carry
        addr = addr.ipv4_mapped or addr.sixtofour or addr
    return addr.is_global and not (addr.is_multicast or addr.is_reserved or addr.is_unspecified)


async def resolve_public(host: str, port: int) -> List[str]:
    """Every address `host` has, or BlockedAddress when any is not public
    (or it has none)."""
    host = (host or "").strip().strip("[]")
    if not host:
        raise BlockedAddress("نشانی خالی است")
    try:
        addrs = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM), timeout=RESOLVE_TIMEOUT)
        except (OSError, asyncio.TimeoutError, UnicodeError):
            raise BlockedAddress(f"نام «{host[:80]}» پیدا نشد") from None
        addrs = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addrs or not all(is_public(a) for a in addrs):
        raise BlockedAddress(REFUSED)
    return addrs


def _split(url: str, schemes) -> Tuple[SplitResult, int]:
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        raise BlockedAddress("نشانی شکل درستی ندارد") from None
    if parts.scheme.lower() not in schemes:
        raise BlockedAddress("فقط نشانی " + " یا ".join(schemes) + " پذیرفته می‌شود")
    if not parts.hostname:
        raise BlockedAddress("نشانی شکل درستی ندارد")
    return parts, port or (443 if parts.scheme.lower() == "https" else 80)


async def check_url(url: str, schemes=("http", "https")) -> List[str]:
    """A URL the server may fetch: one of `schemes`, no user:password@ part,
    and a host that is public all the way down. Returns its addresses."""
    parts, port = _split(url, schemes)
    if parts.username is not None or parts.password is not None:
        raise BlockedAddress("نشانی نباید نام کاربری یا رمز داشته باشد")
    return await resolve_public(parts.hostname or "", port)


async def check_proxy(url: str) -> List[str]:
    """A proxy URL — scheme://[user:pass@]host:port — whose host is public."""
    parts, port = _split(url, ("http", "https", "socks5", "socks5h", "socks4"))
    return await resolve_public(parts.hostname or "", port)


def _at(parts: SplitResult, ip: str) -> str:
    """The URL with its host replaced by the address that was checked."""
    host = f"[{ip}]" if ":" in ip else ip
    return parts._replace(netloc=f"{host}:{parts.port}" if parts.port else host).geturl()


async def fetch_text(url: str, *, max_bytes: int = 1_000_000, timeout: float = 30.0) -> str:
    """GET a public URL and return its body as text, at most `max_bytes` of
    it, within `timeout` seconds overall — redirects included, each one
    checked as the first was."""
    async def go():
        nonlocal url
        for _ in range(MAX_REDIRECTS + 1):
            addrs = await check_url(url)
            parts = urlsplit(url)
            # Connect to the address just checked; the name travels in the
            # Host header, and in TLS as SNI and the certificate's name.
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", _at(parts, addrs[0]), headers={"Host": parts.netloc},
                                         extensions={"sni_hostname": parts.hostname}) as r:
                    if r.is_redirect:
                        url = urljoin(url, r.headers["location"])
                        continue
                    r.raise_for_status()
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) >= max_bytes:
                            break
                    return bytes(body[:max_bytes]).decode(r.encoding or "utf-8", errors="replace")
        raise BlockedAddress("تغییر مسیر بیش از حد")
    return await asyncio.wait_for(go(), timeout)
