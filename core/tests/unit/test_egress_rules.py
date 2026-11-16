"""Node egress rules for connected agents (pre-public-hardening S1).

The rules close private networks, the VPS metadata service and direct DNS to the agents by their
addresses; the core or the node gateway in the same network must stay outside that range, or the
model path through the tunnel breaks (seen on the stand 16.11.2026).
"""

import ipaddress
import re
from pathlib import Path

from yorath.distribution.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = (ROOT / "installer/egress/yorath.nft").read_text()
MANIFEST = load_manifest(ROOT / "distribution.yaml")


def agents_range() -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
    m = re.search(r"set agents4 \{[^}]*elements = \{ ([\d.]+)-([\d.]+) \}", RULES)
    assert m, "the agents4 set is a range of addresses"
    return ipaddress.IPv4Address(m[1]), ipaddress.IPv4Address(m[2])


def test_every_connected_agent_address_is_in_the_closed_range():
    low, high = agents_range()
    for name, spec in MANIFEST.connected_agents.items():
        addr = ipaddress.IPv4Address(f"172.31.0.{spec.address}")
        assert low <= addr <= high, f"{name} at {addr} would keep private networks open"
    # any address the manifest may give (10..250) is covered, not only today's two
    assert (low, high >= ipaddress.IPv4Address("172.31.0.250")) == (
        ipaddress.IPv4Address("172.31.0.10"),
        True,
    )


def test_the_core_and_the_node_gateway_stay_outside_the_range():
    low, _ = agents_range()
    # Docker gives them the first free dynamic address in yorath_agents
    for dynamic in ("172.31.0.2", "172.31.0.3", "172.31.0.9"):
        assert ipaddress.IPv4Address(dynamic) < low


def test_agents_lose_metadata_private_networks_host_ports_and_direct_dns():
    assert re.search(r"ip daddr 169\.254\.0\.0/16 drop", RULES)
    assert re.search(r"ip saddr @agents4 ip daddr @private4 drop", RULES)
    assert re.search(r"ip saddr @agents4 udp dport \{ 53, 123 \} drop", RULES)
    # on the host only the proxy ports, and that before the general "containers may" return
    guard = RULES[RULES.index("chain guard") :]
    assert (
        guard.index("ip saddr @agents4 tcp dport { 3129, 3130 } return")
        < guard.index("ip saddr @agents4 drop")
        < guard.index('iifname { "docker0", "br-*" } return')
    )
