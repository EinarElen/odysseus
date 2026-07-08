"""Harness integration layer.

Harnesses can run their own turn machinery, but Odysseus owns the product
session shell. Adapters translate events and cooperative control-yield requests
between the harness runtime and Odysseus.
"""

from .base import (
    CONTROL_REQUEST_TYPES,
    HarnessAdapter,
    HarnessCapabilities,
    HarnessControlRequest,
    HarnessControlResult,
    HarnessEvent,
    HarnessSessionRef,
    harness_config_from_session,
    is_harness_session,
)
from .registry import get_harness_adapter, list_harness_capabilities, list_harness_ids
from .sdk import (
    HarnessControlBroker,
    HarnessSdkError,
    HarnessToolBroker,
    HarnessToolCall,
    HarnessToolDefinition,
    HarnessToolResult,
    OdysseusControlBroker,
    OdysseusToolBroker,
    SdkHarnessAdapter,
)

__all__ = [
    "HarnessAdapter",
    "HarnessCapabilities",
    "HarnessControlBroker",
    "HarnessControlRequest",
    "HarnessControlResult",
    "HarnessEvent",
    "HarnessSdkError",
    "HarnessSessionRef",
    "HarnessToolBroker",
    "HarnessToolCall",
    "HarnessToolDefinition",
    "HarnessToolResult",
    "OdysseusControlBroker",
    "OdysseusToolBroker",
    "SdkHarnessAdapter",
    "get_harness_adapter",
    "harness_config_from_session",
    "is_harness_session",
    "list_harness_capabilities",
    "list_harness_ids",
    "CONTROL_REQUEST_TYPES",
]
