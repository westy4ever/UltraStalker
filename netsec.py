# -*- coding: utf-8 -*-
"""Small network-security helpers shared by optional metadata clients."""
from __future__ import absolute_import

import urllib.error
import urllib.parse
import urllib.request
import socket
import ipaddress
import http.client
import ssl


def validate_http_url(url, require_https=False, allow_userinfo=False):
    """Validate an HTTP(S) URL before it reaches urllib/http.client.

    Reject parser-ambiguity and header-injection primitives centrally so every
    network path applies the same baseline rules. Query-string credentials are
    allowed for IPTV compatibility; URL userinfo is rejected unless a caller
    explicitly opts in.
    """
    value = str(url or "")
    if not value or value != value.strip():
        raise urllib.error.URLError("URL is empty or has surrounding whitespace")
    if len(value) > 8192:
        raise urllib.error.URLError("URL exceeds the supported length")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        raise urllib.error.URLError("URL contains control characters")
    if "\\" in value:
        raise urllib.error.URLError("URL contains a backslash")
    try:
        parsed = urllib.parse.urlsplit(value)
        scheme = (parsed.scheme or "").lower()
        if require_https:
            if scheme != "https":
                raise urllib.error.URLError("Credential-bearing requests require HTTPS")
        elif scheme not in ("http", "https"):
            raise urllib.error.URLError("URL must use HTTP or HTTPS")
        if not parsed.hostname:
            raise urllib.error.URLError("URL is missing a hostname")
        # Force port parsing here; malformed/out-of-range ports otherwise fail
        # later and inconsistently across urllib/http.client call sites.
        _ = parsed.port
        if not allow_userinfo and (parsed.username is not None or parsed.password is not None):
            raise urllib.error.URLError("URL userinfo is not allowed")
    except (ValueError, UnicodeError) as exc:
        raise urllib.error.URLError("Invalid URL: %s" % exc)
    return value


class SameOriginHTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only when the HTTPS origin (scheme/host/port) is unchanged.

    Credential-bearing metadata requests must never forward Authorization,
    API-key, cookie, signed AWS headers or request bodies to another origin.
    """
    @staticmethod
    def _origin(url):
        parsed = urllib.parse.urlsplit(str(url or ""))
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        port = parsed.port or (443 if scheme == "https" else 80 if scheme == "http" else None)
        return scheme, host, port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = validate_http_url(urllib.parse.urljoin(req.full_url, newurl), require_https=True)
        old_origin = self._origin(req.full_url)
        new_origin = self._origin(target)
        if old_origin[0] != "https" or new_origin[0] != "https":
            raise urllib.error.HTTPError(target, code, "HTTPS redirect downgrade blocked", headers, fp)
        if old_origin != new_origin:
            raise urllib.error.HTTPError(target, code, "Cross-origin credential redirect blocked", headers, fp)
        return super(SameOriginHTTPSRedirectHandler, self).redirect_request(req, fp, code, msg, headers, target)


_CREDENTIAL_OPENER = urllib.request.build_opener(SameOriginHTTPSRedirectHandler())


class ProviderRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Safer redirects for user-configured M3U/Xtream provider requests.

    Provider URLs may legitimately use HTTP and may redirect to a CDN, so this
    policy is intentionally less strict than credential_urlopen(). It still
    prevents HTTPS downgrade and strips credential-like headers whenever the
    origin changes. Query-string credentials are not copied by urllib when a
    server supplies a different redirect target.
    """
    SENSITIVE = {
        "authorization", "cookie", "proxy-authorization", "referer",
        "x-api-key", "api-key", "x-auth-token", "x-access-token",
    }

    @staticmethod
    def _origin(url):
        parsed = urllib.parse.urlsplit(str(url or ""))
        scheme = (parsed.scheme or "").lower()
        host = _normalized_host(parsed.hostname)
        port = parsed.port or (443 if scheme == "https" else 80 if scheme == "http" else None)
        return scheme, host, port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = validate_http_url(urllib.parse.urljoin(req.full_url, newurl))
        old_origin = self._origin(req.full_url)
        new_origin = self._origin(target)
        if new_origin[0] not in ("http", "https"):
            raise urllib.error.HTTPError(target, code, "Non-HTTP provider redirect blocked", headers, fp)
        if old_origin[0] == "https" and new_origin[0] != "https":
            raise urllib.error.HTTPError(target, code, "HTTPS provider redirect downgrade blocked", headers, fp)
        redirected = super(ProviderRedirectHandler, self).redirect_request(req, fp, code, msg, headers, target)
        if redirected is None:
            return None
        if old_origin != new_origin:
            for mapping in (redirected.headers, redirected.unredirected_hdrs):
                for key in list(mapping):
                    if key.lower() in self.SENSITIVE:
                        del mapping[key]
        return redirected


_PROVIDER_OPENER = urllib.request.build_opener(ProviderRedirectHandler())


def provider_urlopen(request, timeout):
    """Open a user-configured M3U/Xtream HTTP(S) URL with safer redirects."""
    validate_http_url(getattr(request, "full_url", request))
    return _PROVIDER_OPENER.open(request, timeout=timeout)


def credential_urlopen(request, timeout):
    """Open a credential-bearing HTTPS request with strict redirect policy."""
    validate_http_url(getattr(request, "full_url", request), require_https=True)
    return _CREDENTIAL_OPENER.open(request, timeout=timeout)



def _normalized_host(value):
    return str(value or "").strip().rstrip(".").lower()


def _resolve_remote_media_target(url, trusted_private_origins=()):
    """Parse and resolve a media URL once, returning validated connection data.

    The returned IP list is intended to be used for the actual socket connection,
    not merely as a preflight check. This closes the DNS-rebinding/TOCTOU gap
    where a hostname could resolve public during validation and private during a
    second resolver call in the HTTP library.
    """
    value = validate_http_url(url)
    try:
        parsed = urllib.parse.urlsplit(value)
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise urllib.error.URLError("Remote media URL must use HTTP or HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise urllib.error.URLError("Remote media URL userinfo is not allowed")
        host = _normalized_host(parsed.hostname)
        if not host:
            raise urllib.error.URLError("Remote media URL is missing a hostname")
        port = parsed.port or (443 if scheme == "https" else 80)
    except (ValueError, UnicodeError) as exc:
        raise urllib.error.URLError("Invalid remote media URL: %s" % exc)

    trusted = set()
    for item in (trusted_private_origins or ()):
        try:
            origin = urllib.parse.urlsplit(str(item or "").strip())
            origin_scheme = (origin.scheme or "").lower()
            origin_host = _normalized_host(origin.hostname)
            if origin_scheme not in ("http", "https") or not origin_host:
                continue
            origin_port = origin.port or (443 if origin_scheme == "https" else 80)
            trusted.add((origin_scheme, origin_host, origin_port))
        except (ValueError, UnicodeError):
            continue
    trusted_origin = (scheme, host, port) in trusted
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise urllib.error.URLError("Remote media hostname could not be resolved: %s" % exc)

    endpoints = []
    seen = set()
    for family, socktype, proto, _canonname, sockaddr in infos:
        try:
            raw = sockaddr[0].split("%", 1)[0]
        except Exception:
            continue
        key = (family, socktype, proto, sockaddr)
        if key in seen:
            continue
        seen.add(key)
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            raise urllib.error.URLError("Remote media hostname resolved to an invalid address")
        # Never permit destinations that are meaningful only to the receiver
        # itself or its local link, even when a hostname is trusted. Private
        # RFC1918/ULA addresses are allowed only for an explicitly trusted host
        # (normally the configured portal host).
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
            raise urllib.error.URLError("Remote media destination class is blocked")
        if not address.is_global and not trusted_origin:
            raise urllib.error.URLError("Remote media destination is not publicly routable")
        endpoints.append((family, socktype or socket.SOCK_STREAM, proto, sockaddr))
    if not endpoints:
        raise urllib.error.URLError("Remote media hostname resolved to no usable address")
    return value, scheme, host, port, tuple(endpoints)


def validate_remote_media_url(url, trusted_private_origins=()):
    """Validate an untrusted artwork/media URL before a receiver-side fetch."""
    value, _scheme, _host, _port, _endpoints = _resolve_remote_media_target(
        url, trusted_private_origins
    )
    return value


class _PinnedMediaMixin(object):
    """Connect to a validated IP while preserving the original HTTP hostname."""
    trusted_private_origins = ()

    def _validated_endpoints(self):
        scheme = "https" if isinstance(self, http.client.HTTPSConnection) else "http"
        host = self.host
        # HTTPConnection may carry an IPv6 host without brackets; urlsplit needs them.
        display_host = host
        try:
            if ":" in display_host and not display_host.startswith("["):
                ipaddress.ip_address(display_host)
                display_host = "[" + display_host + "]"
        except ValueError:
            pass
        url = "%s://%s:%d/" % (scheme, display_host, int(self.port))
        _value, _scheme, _host, _port, endpoints = _resolve_remote_media_target(
            url, self.trusted_private_origins
        )
        return endpoints

    def _connect_validated_socket(self):
        last_error = None
        for family, socktype, proto, sockaddr in self._validated_endpoints():
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(self.timeout)
                if self.source_address:
                    sock.bind(self.source_address)
                sock.connect(sockaddr)
                return sock
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        if last_error is not None:
            raise last_error
        raise OSError("No validated remote media address available")


class _PinnedMediaHTTPConnection(_PinnedMediaMixin, http.client.HTTPConnection):
    def connect(self):
        self.sock = self._connect_validated_socket()
        if self._tunnel_host:
            self._tunnel()


class _PinnedMediaHTTPSConnection(_PinnedMediaMixin, http.client.HTTPSConnection):
    def connect(self):
        self.sock = self._connect_validated_socket()
        server_hostname = self.host
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _PinnedMediaHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, trusted_private_origins=()):
        super(_PinnedMediaHTTPHandler, self).__init__()
        self.trusted_private_origins = tuple(trusted_private_origins or ())

    def http_open(self, req):
        trusted = self.trusted_private_origins

        class Connection(_PinnedMediaHTTPConnection):
            trusted_private_origins = trusted

        return self.do_open(Connection, req)


class _PinnedMediaHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, trusted_private_origins=(), context=None):
        context = context or ssl.create_default_context()
        super(_PinnedMediaHTTPSHandler, self).__init__(context=context)
        self.trusted_private_origins = tuple(trusted_private_origins or ())

    def https_open(self, req):
        trusted = self.trusted_private_origins

        class Connection(_PinnedMediaHTTPSConnection):
            trusted_private_origins = trusted

        # Do not depend on urllib's private _check_hostname attribute: it is
        # absent on newer Python/OpenBH builds. HTTPSConnection derives hostname
        # verification from the supplied SSLContext, whose default context keeps
        # certificate and hostname checks enabled.
        return self.do_open(Connection, req, context=self._context)


class SafeMediaRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate every media redirect and strip sensitive headers cross-origin."""
    SENSITIVE = {
        "authorization", "cookie", "proxy-authorization", "referer",
        "x-api-key", "api-key", "x-auth-token", "x-access-token",
    }

    def __init__(self, trusted_private_origins=()):
        super(SafeMediaRedirectHandler, self).__init__()
        self.trusted_private_origins = tuple(trusted_private_origins or ())

    @staticmethod
    def _origin(url):
        parts = urllib.parse.urlsplit(str(url or ""))
        scheme = (parts.scheme or "").lower()
        host = _normalized_host(parts.hostname)
        port = parts.port or (443 if scheme == "https" else 80 if scheme == "http" else None)
        return scheme, host, port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        source_scheme = (urllib.parse.urlsplit(str(req.full_url or "")).scheme or "").lower()
        target_scheme = (urllib.parse.urlsplit(str(target or "")).scheme or "").lower()
        if source_scheme == "https" and target_scheme != "https":
            raise urllib.error.HTTPError(target, code, "HTTPS media redirect downgrade blocked", headers, fp)
        validate_remote_media_url(target, self.trusted_private_origins)
        redirected = super(SafeMediaRedirectHandler, self).redirect_request(req, fp, code, msg, headers, target)
        if redirected is None:
            return None
        if self._origin(req.full_url) != self._origin(target):
            for mapping in (redirected.headers, redirected.unredirected_hdrs):
                for key in list(mapping):
                    if key.lower() in self.SENSITIVE:
                        del mapping[key]
        return redirected



def build_safe_media_opener(trusted_private_origins=()):
    """Build a media opener with redirect validation and DNS-pinned sockets.

    Environment HTTP(S) proxies are deliberately disabled for untrusted media
    fetches so a proxy cannot become an alternate route around destination
    validation. Credential-bearing metadata requests use a separate opener.
    """
    trusted = tuple(trusted_private_origins or ())
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        SafeMediaRedirectHandler(trusted_private_origins=trusted),
        _PinnedMediaHTTPHandler(trusted_private_origins=trusted),
        _PinnedMediaHTTPSHandler(trusted_private_origins=trusted),
    )

class HTTPSOnlySafeMediaRedirectHandler(SafeMediaRedirectHandler):
    """Safe media redirects that require HTTPS and can enforce a host allowlist."""
    def __init__(self, trusted_private_origins=(), allowed_hosts=()):
        super(HTTPSOnlySafeMediaRedirectHandler, self).__init__(trusted_private_origins)
        self.allowed_hosts = frozenset(
            _normalized_host(host) for host in (allowed_hosts or ()) if _normalized_host(host)
        )

    def _check_allowed_host(self, target, code, headers, fp):
        parts = urllib.parse.urlsplit(str(target or ""))
        if (parts.scheme or "").lower() != "https":
            raise urllib.error.HTTPError(target, code, "HTTPS media redirect downgrade blocked", headers, fp)
        host = _normalized_host(parts.hostname)
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise urllib.error.HTTPError(target, code, "HTTPS media redirect host blocked", headers, fp)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        self._check_allowed_host(target, code, headers, fp)
        return super(HTTPSOnlySafeMediaRedirectHandler, self).redirect_request(
            req, fp, code, msg, headers, target
        )


def build_safe_https_media_opener(trusted_private_origins=(), allowed_hosts=()):
    """Build a DNS-pinned HTTPS-only media opener.

    ``allowed_hosts`` optionally constrains every redirect destination to an
    explicit hostname set. This is useful for generated CDN URLs such as TMDB,
    where accepting arbitrary public HTTPS redirect targets is unnecessary.
    """
    trusted = tuple(trusted_private_origins or ())
    allowed = tuple(allowed_hosts or ())
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        HTTPSOnlySafeMediaRedirectHandler(
            trusted_private_origins=trusted, allowed_hosts=allowed
        ),
        _PinnedMediaHTTPHandler(trusted_private_origins=trusted),
        _PinnedMediaHTTPSHandler(trusted_private_origins=trusted),
    )

