from enum import StrEnum


class RequestState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"

    def __str__(self) -> str:
        return str(self.value)
