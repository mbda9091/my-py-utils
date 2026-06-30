"""Small text helpers used by CLI packages."""


def greeting(name: str) -> str:
    """Build a friendly greeting."""
    normalized_name = name.strip() or "world"
    return f"Hello, {normalized_name}!"
