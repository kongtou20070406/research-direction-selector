"""Optional project lifecycle providers, loaded only when their hooks are needed."""

_providers = {}
_modules = {"advisor": "rds_owned_advisor", "quick": "rds_quick_ledger"}


def register(name, provider):
    if name not in _modules:
        raise ValueError(f"Unknown project hook provider: {name}")
    _providers[name] = provider


def provider(name):
    if name not in _modules:
        raise ValueError(f"Unknown project hook provider: {name}")
    if name not in _providers:
        if name == "advisor":
            import rds_owned_advisor  # noqa: F401
        elif name == "quick":
            import rds_quick_ledger  # noqa: F401
    return _providers[name]
