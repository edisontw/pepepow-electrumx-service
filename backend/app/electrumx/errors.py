class ElectrumXError(Exception):
    pass


class ElectrumXTimeoutError(ElectrumXError):
    pass


class ElectrumXUnavailableError(ElectrumXError):
    pass


class ElectrumXProtocolError(ElectrumXError):
    pass
