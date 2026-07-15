"""Определение собственного IP-адреса машины в сети — без хардкода 127.0.0.1.

При обращении к своим же сервисам система должна использовать актуальный IP той
машины, на которой запущена, а не петлевой адрес. IP определяется динамически и
переопределяется автоматически, если адрес сменился в процессе работы (короткий
кэш). Петлевой адрес возможен ТОЛЬКО как крайний резерв, если у машины нет ни
одного сетевого интерфейса (сети нет вообще).
"""
import socket
import time

# Значения host, означающие «эта же машина» — их подменяем на актуальный IP.
SELF_HOST_MARKERS = {"", "0.0.0.0", "::", "localhost", "127.0.0.1", "auto"}

_cache = {"ip": "", "at": 0.0}
_CACHE_TTL = 10.0  # сек: как часто перепроверяем собственный IP


def _probe_via_udp(target):
    probe = (str(target).strip() if target else "") or "8.8.8.8"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP-connect не отправляет пакетов, только выбирает исходящий интерфейс.
        sock.connect((probe, 9))
        ip = sock.getsockname()[0]
        return ip if ip and not ip.startswith("127.") else ""
    except OSError:
        return ""
    finally:
        sock.close()


def _probe_via_hostname():
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return ""


def _loopback():
    # Единственное место во всём проекте, где может появиться петлевой адрес, и
    # только когда реального сетевого IP не существует (нет сети). При наличии сети
    # всегда возвращается настоящий IP машины.
    return "127.0.0.1"


def local_ip(target=None):
    """Актуальный IP этой машины в сети.

    target — если задан (IP прибора/адресата), берётся интерфейс, смотрящий на
    него; иначе интерфейс маршрута по умолчанию (результат кэшируется на несколько
    секунд, поэтому смена IP подхватывается автоматически)."""
    if target:
        return _probe_via_udp(target) or _probe_via_hostname() or _loopback()
    now = time.monotonic()
    if _cache["ip"] and (now - _cache["at"]) < _CACHE_TTL:
        return _cache["ip"]
    ip = _probe_via_udp(None) or _probe_via_hostname() or _loopback()
    _cache["ip"] = ip
    _cache["at"] = now
    return ip


def resolve_self_host(host, target=None):
    """Если host указывает на «эту же машину» (пусто/0.0.0.0/localhost/127.0.0.1/
    auto) — вернуть актуальный IP машины; иначе вернуть host без изменений."""
    text = str(host or "").strip()
    if text.lower() in SELF_HOST_MARKERS:
        return local_ip(target)
    return text


def resolve_url_self_host(url, target=None):
    """Заменить host в URL на актуальный IP машины, если это self-маркер."""
    from urllib.parse import urlsplit, urlunsplit
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return url
    if not parts.hostname:
        return url
    resolved = resolve_self_host(parts.hostname, target)
    if resolved == parts.hostname:
        return url
    netloc = f"{resolved}:{parts.port}" if parts.port else resolved
    if parts.username:
        auth = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{auth}@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
