"""Published observation labels, vuoro-model-family/v1. No selection API."""
VERSION = "vuoro-model-family/v1"
PREFIXES = (("claude-opus-", "claude-opus"), ("claude-sonnet-", "claude-sonnet"),
            ("claude-haiku-", "claude-haiku"), ("gpt-", "openai-gpt"))


def classify(model_id: str) -> str:
    return next((family for prefix, family in PREFIXES if model_id.startswith(prefix)), "unclassified")
