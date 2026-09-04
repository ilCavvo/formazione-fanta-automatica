"""Client HTTP "gentile" verso i siti sorgente.

Requisiti dal brief: niente scraping aggressivo. Qui dentro:
- un solo User-Agent dichiarato e onesto,
- delay minimo garantito fra due richieste allo stesso host,
- retry con backoff esponenziale solo su errori transitori,
- cache su disco a TTL, cosi' i run ravvicinati (24h e 2h prima della deadline)
  non ribattono inutilmente sulle stesse pagine.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

#: Status che ha senso ritentare; tutto il resto e' un errore vero.
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    text: str
    status_code: int
    from_cache: bool = False


class PoliteClient:
    """Wrapper httpx con rate limiting per host e cache su disco."""

    def __init__(
        self,
        user_agent: str,
        delay_s: float = 2.0,
        timeout_s: float = 30.0,
        max_retries: int = 3,
        backoff_base_s: float = 2.0,
        cache_dir: Path | None = None,
        cache_ttl_minutes: int = 0,
    ) -> None:
        self.delay_s = max(0.0, delay_s)
        self.max_retries = max(0, max_retries)
        self.backoff_base_s = backoff_base_s
        self.cache_dir = cache_dir
        self.cache_ttl_s = max(0, cache_ttl_minutes) * 60
        self._last_request_at: dict[str, float] = {}
        self._client = httpx.Client(
            timeout=timeout_s,
            follow_redirects=True,
            headers={
                "User-Agent": user_agent,
                # Alcune pagine servono markup diverso senza questi header.
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
            },
        )
        if self.cache_dir is not None and self.cache_ttl_s:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- cache --------------------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None or not self.cache_ttl_s:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl_s:
            return None
        log.debug("cache hit: %s", url)
        return path.read_text(encoding="utf-8")

    def _write_cache(self, url: str, text: str) -> None:
        path = self._cache_path(url)
        if path is not None:
            path.write_text(text, encoding="utf-8")

    # -- rate limiting ------------------------------------------------------

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_request_at.get(host)
        if last is not None:
            wait = self.delay_s - (time.monotonic() - last)
            if wait > 0:
                log.debug("throttle %s: attendo %.1fs", host, wait)
                time.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    # -- API ----------------------------------------------------------------

    def get(self, url: str, *, use_cache: bool = True) -> FetchResult:
        if use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return FetchResult(url=url, text=cached, status_code=200, from_cache=True)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(url)
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                log.warning("GET %s fallita (tentativo %d): %s", url, attempt + 1, exc)
            else:
                if response.status_code == 200:
                    self._write_cache(url, response.text)
                    return FetchResult(url=str(response.url), text=response.text,
                                       status_code=response.status_code)
                if response.status_code not in _RETRYABLE:
                    raise httpx.HTTPStatusError(
                        f"GET {url} -> HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
                log.warning("GET %s -> HTTP %s (tentativo %d)", url,
                            response.status_code, attempt + 1)

            if attempt < self.max_retries:
                delay = self.backoff_base_s * (2**attempt)
                log.info("retry fra %.1fs...", delay)
                time.sleep(delay)

        raise RuntimeError(
            f"GET {url} fallita dopo {self.max_retries + 1} tentativi"
        ) from last_error


def client_from_config(cfg, cache_dir: Path | None = None) -> PoliteClient:
    """Costruisce il client leggendo la sezione `http` della config."""
    return PoliteClient(
        user_agent=cfg.get("http.user_agent", "fantabot/0.1"),
        delay_s=float(cfg.get("http.delay_between_requests_s", 2.0)),
        timeout_s=float(cfg.get("http.timeout_s", 30)),
        max_retries=int(cfg.get("http.max_retries", 3)),
        backoff_base_s=float(cfg.get("http.backoff_base_s", 2.0)),
        cache_dir=cache_dir,
        cache_ttl_minutes=int(cfg.get("http.cache_ttl_minutes", 0)),
    )
