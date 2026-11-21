"""HTTP client shared by the desktop connectors: auth/VPN error mapping and 429 back-off."""
from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def retry_delay(value: str | None, attempt: int) -> float:
    """Seconds to wait for a Retry-After header (delta-seconds or HTTP-date); exponential when absent/garbled."""
    default = float(2 ** attempt)
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return default
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - _utcnow()).total_seconds())


class ServiceError(Exception):
    def __init__(self, service: str, message: str):
        super().__init__(message)
        self.service = service
        self.message = message


class AuthError(ServiceError):
    pass


class Unreachable(ServiceError):
    pass


class NotFound(ServiceError):
    pass


class ApiClient:
    def __init__(self, service: str, base_url: str, headers: dict, transport=None, sleep=time.sleep,
                 max_tries: int = 5):
        self.service = service
        self.sleep = sleep
        self.max_tries = max_tries
        self.client = httpx.Client(base_url=base_url, headers=headers, transport=transport,
                                   timeout=60.0, follow_redirects=True)

    def _check(self, response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise AuthError(self.service, f"{self.service} token rejected")
        if response.status_code == 404:
            raise NotFound(self.service, f"{self.service}: not found ({response.request.url.path})")
        if response.status_code >= 400:
            raise ServiceError(self.service, f"{self.service} returned HTTP {response.status_code}")

    def _unreachable(self, url: str) -> Unreachable:
        host = self.client.base_url.join(url).host or self.service
        return Unreachable(self.service, f"Cannot reach {host} — are you on the VPN?")

    def _map_transport(self, e: httpx.TransportError, url: str) -> ServiceError:
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
            return self._unreachable(url)
        return ServiceError(self.service, f"{self.service} connection failed ({type(e).__name__})")

    def _backoff(self, response: httpx.Response, attempt: int) -> None:
        if attempt < self.max_tries - 1:  # no point waiting after the last try
            self.sleep(retry_delay(response.headers.get("Retry-After"), attempt))

    def _rate_limited(self) -> ServiceError:
        return ServiceError(self.service, f"{self.service} is rate limiting requests; try again later")

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self.max_tries):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as e:
                raise self._map_transport(e, url) from e
            if response.status_code == 429:
                self._backoff(response, attempt)
                continue
            self._check(response)
            return response
        raise self._rate_limited()

    def get_json(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params).json()

    def post_json(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json=body).json()

    def download(self, url_or_path: str, dest: Path, max_bytes: int | None) -> bool:
        """Save to dest; returns False (leaving no file) if the body exceeds max_bytes."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(self.max_tries):
            tmp = None
            try:
                with self.client.stream("GET", url_or_path) as response:
                    if response.status_code == 429:
                        self._backoff(response, attempt)
                        continue
                    self._check(response)
                    if max_bytes is not None and int(response.headers.get("Content-Length") or 0) > max_bytes:
                        return False
                    size = 0
                    with tempfile.NamedTemporaryFile("wb", dir=dest.parent, prefix=dest.name + ".",
                                                     suffix=".part", delete=False) as out:
                        tmp = Path(out.name)
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if max_bytes is not None and size > max_bytes:
                                return False
                            out.write(chunk)
                os.replace(tmp, dest)
                tmp = None
                return True
            except httpx.TransportError as e:
                raise self._map_transport(e, url_or_path) from e
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
        raise self._rate_limited()
