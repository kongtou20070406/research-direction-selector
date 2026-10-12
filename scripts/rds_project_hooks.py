"""Optional project lifecycle providers, loaded only when their hooks are needed."""

from importlib import import_module


_providers = {}
_modules = {"advisor": "rds_owned_advisor", "quick": "rds_quick"}


def register(name, provider):
    if name not in _modules:
        raise ValueError(f"Unknown project hook provider: {name}")
    _providers[name] = provider


def provider(name):
    if name not in _modules:
        raise ValueError(f"Unknown project hook provider: {name}")
    if name not in _providers:
        import_module(_modules[name])
    return _providers[name]
