"""Unified verifier registration and dispatch for certificate checkers.

A verifier declares the statement kinds it owns, the semantics it reasons about
and the assurance category it may report, then implements read_spec, verify and
check_certificate. Trusted Python code registers the concrete class with
``@register_verifier``; declarative input cannot register executable code, and
``get_verifier`` routes one statement kind to its single owning verifier.

This is an L0 leaf module: it imports only the standard library and rds_core.
It must not import rds_verify or any domain verifier, so the framework and the
domain adapters can depend on it without introducing a reverse circular
dependency. Nothing here searches proofs, reads a project or grants
authorization.
"""
import abc
import re

from rds_core import object_digest

KIND_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}")
ASSURANCE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
MAX_NAME_LENGTH = 80
MAX_TEXT_LENGTH = 2048
MAX_VERSION_LENGTH = 32
METHODS = ("read_spec", "verify", "check_certificate")


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _bounded_text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


class VerifierSpec(abc.ABC):
    """One registered kind family with a declared scope and assurance category.

    A concrete subclass defines the four class attributes and the three
    methods. Registration validates that contract; the declaration itself
    neither selects a backend nor asserts that a claim is true.
    """

    name: str
    kinds: set
    semantics: str
    assurance_level: str
    version: str = "1"

    @abc.abstractmethod
    def read_spec(self, spec):
        """Return the normalized declaration, or raise ValueError."""

    @abc.abstractmethod
    def verify(self, spec):
        """Search for evidence and return a result mapping."""

    @abc.abstractmethod
    def check_certificate(self, spec, certificate):
        """Replay supplied evidence without searching; return a bool."""


def _validate(spec_class):
    """Return the declared name and sorted kinds, or raise ValueError."""
    _require(isinstance(spec_class, type), "A verifier must be registered as a class")
    _require(issubclass(spec_class, VerifierSpec), "A registered verifier must subclass VerifierSpec")
    name = getattr(spec_class, "name", None) or spec_class.__name__
    _require(isinstance(name, str) and KIND_PATTERN.fullmatch(name),
             "Verifier name must be 1..80 ASCII letters, digits, _, . or -")
    kinds = getattr(spec_class, "kinds", None)
    _require(isinstance(kinds, (set, frozenset)) and len(kinds) >= 1 and
             all(isinstance(kind, str) and KIND_PATTERN.fullmatch(kind) for kind in kinds),
             "Verifier kinds must be a nonempty set of bounded ASCII statement kinds")
    _require(_bounded_text(getattr(spec_class, "semantics", None), MAX_TEXT_LENGTH),
             "Verifier semantics must be bounded nonempty text")
    assurance = getattr(spec_class, "assurance_level", None)
    _require(isinstance(assurance, str) and ASSURANCE_PATTERN.fullmatch(assurance),
             "Verifier assurance_level must be an uppercase category token")
    _require(assurance not in _RESERVED_ASSURANCES,
             "Verifier cannot declare reserved assurance: " + str(assurance))
    _require(_bounded_text(getattr(spec_class, "version", None), MAX_VERSION_LENGTH),
             "Verifier version must be bounded nonempty text")
    _require(not getattr(spec_class, "__abstractmethods__", frozenset()),
             "Verifier must implement every abstract method: " + ", ".join(METHODS))
    for method in METHODS:
        _require(callable(getattr(spec_class, method, None)),
                 "Verifier must implement " + method)
    return name, sorted(kinds)


_REGISTRY = {}
_BY_NAME = {}
_BUILTIN_KINDS = frozenset()
_BUILTIN_NAMES = frozenset()
_RESERVED_ASSURANCES = frozenset({"LEAN_KERNEL_CHECKED"})


def reserve_builtin_routes(routes, names, assurances=()):
    """Reserve application-owned routes before trusted extensions register."""
    global _BUILTIN_KINDS, _BUILTIN_NAMES, _RESERVED_ASSURANCES
    kinds = frozenset(kind for route in routes for kind in route.kinds)
    route_names = frozenset(names)
    _require(not (_REGISTRY.keys() & kinds), "Built-in verifier kind already registered")
    _require(not (_BY_NAME.keys() & route_names), "Built-in verifier name already registered")
    _BUILTIN_KINDS = kinds
    _BUILTIN_NAMES = route_names
    _RESERVED_ASSURANCES = _RESERVED_ASSURANCES | frozenset(assurances)


def register_verifier(spec_class):
    """Register one concrete VerifierSpec subclass as a class decorator.

    Duplicate verifier names and ambiguous statement kinds fail closed. Nothing
    is registered unless every declared kind is still free, so a rejected
    registration never leaves a partial route behind.
    """
    _require(spec_class is not None,
             "Register a verifier class directly: @register_verifier")
    name, kinds = _validate(spec_class)
    _require(name not in _BUILTIN_NAMES, "Verifier name shadows built-in rule: " + name)
    _require(name not in _BY_NAME, "Duplicate verifier name: " + name)
    for kind in kinds:
        _require(kind not in _BUILTIN_KINDS, "Verifier kind shadows built-in route: " + kind)
        _require(kind not in _REGISTRY, "Ambiguous verifier kind: " + kind)
    try:
        verifier = spec_class()
    except TypeError as exc:
        raise ValueError("Verifier must be constructible without arguments: " + str(exc)) from None
    verifier.name = name
    _BY_NAME[name] = verifier
    for kind in kinds:
        _REGISTRY[kind] = verifier
    return spec_class


def get_verifier(kind):
    """Route one statement kind to its single registered verifier instance."""
    _require(_bounded_text(kind, MAX_NAME_LENGTH), "A verifier kind must be bounded nonempty text")
    verifier = _REGISTRY.get(kind)
    _require(verifier is not None, "Unsupported verifier kind: " + kind)
    return verifier


def unregister_verifier(kind):
    """Remove the verifier owning one kind and every kind it owns."""
    verifier = get_verifier(kind)
    for owned in [key for key, value in _REGISTRY.items() if value is verifier]:
        del _REGISTRY[owned]
    for name in [key for key, value in _BY_NAME.items() if value is verifier]:
        del _BY_NAME[name]
    return verifier


def registered_verifiers():
    """Return registered verifier instances in stable name order."""
    return tuple(_BY_NAME[name] for name in sorted(_BY_NAME))


def registered_kinds():
    """Return every routed statement kind in stable order."""
    return tuple(sorted(_REGISTRY))


def describe():
    """Return the registry's declared scope without importing a verifier body."""
    return [{"name": verifier.name, "kinds": sorted(verifier.kinds),
             "semantics": verifier.semantics, "assurance_level": verifier.assurance_level,
             "version": verifier.version}
            for verifier in registered_verifiers()]


def registry_id():
    """Bind a digest to the registered declarations in stable name order."""
    return object_digest({"verifiers": describe()})
