"""OpenHarness tools that expose the MicroServiceFL data layer to the agent.

``build_fl_tools()`` returns the list of ``fl_*`` tool instances; it is called
from ``openharness.tools`` (guarded by try/except) so the harness still works
when this package is absent.
"""

from __future__ import annotations

from openharness.tools.base import BaseTool


def build_fl_tools() -> list[BaseTool]:
    """Instantiate every fault-localization tool."""
    from microservice_fl.tools.cases import ListCasesTool, GetCaseTool
    from microservice_fl.tools.codemap import MapEndpointTool, ClassToJarTool
    from microservice_fl.tools.decompile import DecompileClassTool
    from microservice_fl.tools.signals import (
        ScanServicesTool,
        EndpointAnomalyTool,
        EndpointBreakdownTool,
        TopologyTool,
        ErrorLogsTool,
        SpanErrorsTool,
    )

    return [
        ScanServicesTool(),
        TopologyTool(),
        EndpointAnomalyTool(),
        EndpointBreakdownTool(),
        SpanErrorsTool(),
        ErrorLogsTool(),
        MapEndpointTool(),
        ClassToJarTool(),
        DecompileClassTool(),
        ListCasesTool(),
        GetCaseTool(),
    ]


__all__ = ["build_fl_tools"]
