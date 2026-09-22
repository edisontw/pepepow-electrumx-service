import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import quote, urlsplit


class WebhookUrlError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WebhookResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedWebhookTarget:
    hostname: str
    port: int
    path_and_query: str
    host_header: str
    addresses: tuple[str, ...]


def _is_safe_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


def _validate_hostname(hostname: str) -> str:
    try:
        normalized = hostname.encode("idna").decode("ascii").rstrip(".").lower()
    except UnicodeError as exc:
        raise WebhookUrlError("invalid_webhook_url", "Webhook hostname is invalid.") from exc

    if not normalized:
        raise WebhookUrlError("invalid_webhook_url", "Webhook hostname is missing.")

    blocked = {
        "localhost",
        "metadata.google.internal",
        "metadata.oraclecloud.com",
        "169.254.169.254",
    }
    if normalized in blocked or normalized.endswith(".localhost") or normalized.endswith(".local"):
        raise WebhookUrlError("unsafe_webhook_target", "Webhook target is not public.")

    return normalized


async def resolve_webhook_target(
    url: str,
    *,
    resolver=None,
) -> ResolvedWebhookTarget:
    if not isinstance(url, str) or len(url) > 2048:
        raise WebhookUrlError("invalid_webhook_url", "Webhook URL is invalid.")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise WebhookUrlError("invalid_webhook_url", "Webhook URL contains control characters.")

    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise WebhookUrlError("invalid_webhook_url", "Webhook URL must use HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise WebhookUrlError("invalid_webhook_url", "Webhook URL must not contain userinfo.")
    if parsed.fragment:
        raise WebhookUrlError("invalid_webhook_url", "Webhook URL must not contain a fragment.")
    if parsed.hostname is None:
        raise WebhookUrlError("invalid_webhook_url", "Webhook hostname is missing.")

    hostname = _validate_hostname(parsed.hostname)

    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise WebhookUrlError("invalid_webhook_url", "Webhook port is invalid.") from exc
    if port != 443:
        raise WebhookUrlError("invalid_webhook_url", "Webhook URL must use HTTPS port 443.")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    addresses: list[str] = []
    if literal is not None:
        if not literal.is_global:
            raise WebhookUrlError("unsafe_webhook_target", "Webhook target is not public.")
        addresses = [str(literal)]
    else:
        loop = asyncio.get_running_loop()
        resolve = resolver or loop.getaddrinfo
        try:
            result = await resolve(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise WebhookResolutionError("Webhook hostname could not be resolved.") from exc

        for entry in result:
            sockaddr = entry[4]
            if not sockaddr:
                continue
            address = str(sockaddr[0])
            if address not in addresses:
                addresses.append(address)

        if not addresses:
            raise WebhookResolutionError("Webhook hostname resolved to no addresses.")
        if any(not _is_safe_public_ip(address) for address in addresses):
            raise WebhookUrlError("unsafe_webhook_target", "Webhook target resolved to a non-public address.")

    path = quote(
        parsed.path or "/",
        safe="/%:@-._~!    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"

    host_header = hostname
'()*+,;=",
    )
    if parsed.query:
        query = quote(
            parsed.query,
            safe="=&%:@/?-._~!    return ResolvedWebhookTarget(
        hostname=hostname,
        port=port,
        path_and_query=path,
        host_header=host_header,
        addresses=tuple(addresses),
    )
()*+,;",
        )
        path += f"?{query}"

    host_header = (
        f"[{hostname}]"
        if literal is not None and literal.version == 6
        else hostname
    )
    return ResolvedWebhookTarget(
        hostname=hostname,
        port=port,
        path_and_query=path,
        host_header=host_header,
        addresses=tuple(addresses),
    )
