"""
Fetching an image from a URL the model gives, for the writing MCP server
(claude.ai cannot send a file, only point at one). The URL comes from a
conversation, so it is treated as hostile: it must not reach anything but
the public internet (no cluster services, no home LAN, no metadata
endpoints), and the answer must stay small.

- https only, on port 443, without credentials in the URL;
- every address the host resolves to must be public (is_global);
- redirects are not followed: one could lead anywhere the checks above
  would refuse;
- at most MAX_UPLOAD_BYTES are read, within TIMEOUT_SECONDS.

What the bytes are is the storage service's to check (identify_upload).

The host is resolved again when connecting, so a name that changes its
answer in between (DNS rebinding) could slip past the address check; the
backend's NetworkPolicy (homelab apps/website-backend/networkpolicy.yaml)
is what stops that, by allowing the pod no private, CGNAT or link-local
address and no port but 443 and 587 outside the cluster.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from services.images import MAX_UPLOAD_BYTES
from services.storage import UploadRefusedError, UploadTooLargeError

TIMEOUT_SECONDS = 15


class RemoteImageError(UploadRefusedError):
    """The URL was refused, or nothing usable came back. The message is fit
    to show the model."""


async def public_addresses(host: str) -> list[str]:
    """The addresses `host` resolves to, if all of them are public."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise RemoteImageError(f"Cannot resolve {host}.") from error
    addresses = sorted({str(info[4][0]) for info in infos})
    private = [a for a in addresses if not ipaddress.ip_address(a).is_global]
    if not addresses or private:
        raise RemoteImageError(f"{host} is not on the public internet.")
    return addresses


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False)


async def fetch(url: str) -> bytes:
    """The bytes at `url`, under the rules above."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise RemoteImageError("Only https:// URLs are fetched.")
    if parts.username or parts.password:
        raise RemoteImageError("URLs with credentials are not fetched.")
    try:
        port = parts.port
    except ValueError as error:
        raise RemoteImageError("The URL's port is not a number.") from error
    if port not in (None, 443):
        raise RemoteImageError("Only the standard https port (443) is fetched.")
    await public_addresses(parts.hostname)

    try:
        async with client() as http, http.stream("GET", url) as response:
            if response.is_redirect:
                raise RemoteImageError(
                    "The URL redirects, which is not followed: give the "
                    "image's own URL."
                )
            if response.status_code != 200:
                raise RemoteImageError(
                    f"The URL answered {response.status_code}, not an image."
                )
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data += chunk
                if len(data) > MAX_UPLOAD_BYTES:
                    raise UploadTooLargeError("Image too large (over 20 MB).")
    except httpx.HTTPError as error:
        raise RemoteImageError(f"Could not fetch the URL: {error!r}") from error
    return bytes(data)
