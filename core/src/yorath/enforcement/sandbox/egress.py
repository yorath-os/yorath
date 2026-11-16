"""Squid log into the audit: allowed and blocked domains of one container."""

from collections import Counter
from collections.abc import Iterable
from urllib.parse import urlsplit


def _domain(method: str, url: str) -> str:
    if method == "CONNECT":
        return url.rsplit(":", 1)[0]
    return urlsplit(url).hostname or url


def summarize(
    lines: Iterable[str], client_ip: str, since: float = 0.0
) -> dict[tuple[str, bool], int]:
    """{(domain, allowed): count}. Squid format: time dur client code/status size method url ..."""
    out: Counter[tuple[str, bool]] = Counter()
    for line in lines:
        parts = line.split()
        if len(parts) < 7 or parts[2] != client_ip:
            continue
        if float(parts[0]) < since:  # the address is freed and goes to the next container
            continue
        allowed = "DENIED" not in parts[3]
        out[(_domain(parts[5], parts[6]), allowed)] += 1
    return dict(out)
