"""LAN peer allow-list plus Host/Origin checks; no browser CORS bypass."""
import ipaddress
from urllib.parse import urlsplit


class AccessPolicy:
    def __init__(self, settings, port):
        self.port = int(port)
        self.hosts = {h.casefold() for h in settings.get('allowed_hosts', ['127.0.0.1', 'localhost'])}
        self.networks = [ipaddress.ip_network(n) for n in settings.get('allowed_networks', ['127.0.0.0/8'])]

    def permits_peer(self, peer):
        try:
            address = ipaddress.ip_address(peer)
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            return any(address in network for network in self.networks)
        except ValueError:
            return False

    def authority(self, value):
        """Return a permitted normalized (host, port), without userinfo/paths."""
        if not value or any(c.isspace() or ord(c) < 32 for c in value):
            return None
        try:
            url = urlsplit('//' + value)
            if url.username or url.password or url.path or url.query or url.fragment:
                return None
            port = url.port or 80
            hostname = (url.hostname or '').casefold()
            if hostname not in self.hosts or port != self.port:
                return None
            return hostname, port
        except ValueError:
            return None

    def permits_request(self, peer, host):
        return self.permits_peer(peer) and self.authority(host) is not None

    def permits_origin(self, origin, host):
        if not origin:
            # CLI can omit Origin; state-changing requests still need the custom header.
            return True
        try:
            url = urlsplit(origin)
            if url.scheme != 'http' or url.path or url.query or url.fragment:
                return False
            return self.authority(url.netloc) == self.authority(host) and self.authority(host) is not None
        except ValueError:
            return False
