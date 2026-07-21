"""Typed transport and compatibility failures for callers and the CLI."""


class VuoroClientError(RuntimeError):
    pass


class ClientIncompatibleError(VuoroClientError):
    pass


class OperationNotFoundError(VuoroClientError):
    pass


class InvocationRejectedError(VuoroClientError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status_code = status_code
