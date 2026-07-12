"""FRR evidence collectors (Gate 3 Step 6)."""

from verifiednet.collectors.frr.bgp import BgpSummaryCollector
from verifiednet.collectors.frr.interfaces import InterfaceStateCollector
from verifiednet.collectors.frr.reachability import ReachabilityCollector
from verifiednet.collectors.frr.routes import RoutePresenceCollector
from verifiednet.collectors.frr.running_config import RunningConfigCollector

__all__ = [
    "BgpSummaryCollector",
    "InterfaceStateCollector",
    "ReachabilityCollector",
    "RoutePresenceCollector",
    "RunningConfigCollector",
]
