from enum import StrEnum


class Ec00OaV1RequestState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"

    def __str__(self) -> str:
        return str(self.value)
