class ElectrumXError(Exception):
    """Base class for safe ElectrumX errors."""

    code = "electrumx_error"


class ElectrumXTimeoutError(ElectrumXError):
    """ElectrumX request timed out."""

    code = "electrumx_timeout"


class ElectrumXConnectionError(ElectrumXError):
    """ElectrumX connection failed."""

    code = "electrumx_unavailable"


class ElectrumXUnavailableError(ElectrumXConnectionError):
    """Backward-compatible unavailable alias."""

    code = "electrumx_unavailable"


class ElectrumXProtocolError(ElectrumXError):
    """ElectrumX returned malformed data."""

    code = "electrumx_protocol_error"

    def __init__(self, message: str = "electrumx_protocol_error", *, data: object | None = None) -> None:
        super().__init__(message)
        self.data = data


class ElectrumXMethodError(ElectrumXError):
    """ElectrumX returned a JSON-RPC method error."""

    code = "electrumx_method_error"

    def __init__(self, message: str = "electrumx_method_error", *, data: object | None = None) -> None:
        super().__init__(message)
        self.data = data
