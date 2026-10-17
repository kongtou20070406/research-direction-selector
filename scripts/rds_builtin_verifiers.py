"""Explicit builtin adapters loaded by rds_verify's composition root."""
from rds_verify_registry import VerifierSpec
from rds_verify_types import SEMANTICS, require


def _load_builtin_module(name):
    """Load only source-owned adapters, lazily, using explicit Python imports.

    This allowlist is code, not configuration. Domain methods and their optional
    backend/fallback policies remain owned by the original adapter.
    """
    if name == "rds_scalar_verify":
        import rds_scalar_verify as adapter
    elif name == "rds_lean_verify":
        import rds_lean_verify as adapter
    elif name == "rds_statistical_verify":
        import rds_statistical_verify as adapter
    elif name == "rds_affine_toolchain":
        import rds_affine_toolchain as adapter
    elif name == "rds_dynamics_verify":
        import rds_dynamics_verify as adapter
    elif name == "rds_matrix_verify":
        import rds_matrix_verify as adapter
    elif name == "rds_scale_verify":
        import rds_scale_verify as adapter
    elif name == "rds_nn_verify":
        import rds_nn_verify as adapter
    elif name == "rds_tensor_verify":
        import rds_tensor_verify as adapter
    elif name == "rds_disk_cover_verify":
        import rds_disk_cover_verify as adapter
    elif name == "rds_rational_voronoi_verify":
        import rds_rational_voronoi_verify as adapter
    elif name == "rds_theory_progression":
        import rds_theory_progression as adapter
    else:
        raise ValueError("Unavailable builtin verifier implementation: " + str(name))
    return adapter


def builtin_module(name):
    try:
        return _load_builtin_module(name)
    except ImportError as exc:
        raise ValueError("Unavailable builtin verifier implementation: " + str(name) +
                         "; " + str(exc)) from exc


class BuiltinVerifier(VerifierSpec):
    """Preserve a trusted ProofRule's domain APIs and source dependencies."""

    def __init__(self, rule):
        self.rule = rule
        self.semantics = SEMANTICS
        self.assurance_level = "CERTIFICATE_CHECKED"

    @property
    def name(self):
        return self.rule.name

    @property
    def kinds(self):
        return frozenset(self.rule.kinds)

    @property
    def version(self):
        return self.rule.version

    @property
    def module(self):
        return self.rule.module

    @property
    def support_files(self):
        return self.rule.support_files

    def read_spec(self, spec):
        return spec

    def verify(self, spec, *, max_work_units=None):
        return self.rule.generate(self.read_spec(spec), max_work_units=max_work_units)

    def check_certificate(self, spec, certificate):
        return self.rule.check(self.read_spec(spec), certificate)

    def canonical_statement(self, statement):
        return self.rule.canonical_statement(statement)

    def checked_assurance(self, certificate):
        return self.rule.checked_assurance(certificate)


class ConjunctionVerifier(VerifierSpec):
    name = "logic.and_intro"
    kinds = frozenset({"all"})
    version = "1"
    semantics = SEMANTICS
    assurance_level = "CERTIFICATE_CHECKED"
    module = "rds_builtin_verifiers"
    support_files = ()

    def read_spec(self, spec):
        require(isinstance(spec, dict) and set(spec) == {"kind", "of"} and
                spec["kind"] == "all", "Invalid conjunction statement")
        refs = spec["of"]
        require(isinstance(refs, list) and 1 <= len(refs) <= 64 and
                all(isinstance(ref, str) for ref in refs) and len(set(refs)) == len(refs),
                "Conjunction requires unique named premises")
        return spec

    def verify(self, spec):
        raise ValueError("Conjunction requires checked theorem premises")

    def check_certificate(self, spec, certificate):
        raise ValueError("Conjunction requires checked theorem premises")

    def compose(self, spec, outcomes):
        refs = self.read_spec(spec)["of"]
        statuses = [outcomes[ref]["status"] for ref in refs]
        require(all(status in {"PASS", "FAIL", "UNKNOWN"} for status in statuses),
                "Invalid conjunction premise status")
        status = "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
        return {"status": status, "proof": {"rule": self.name, "premises": refs, "verdict": status}}

    def replay_composition(self, spec, certificate, outcomes):
        require(isinstance(certificate, dict) and set(certificate) == {"rule", "premises", "verdict"},
                "Invalid conjunction proof")
        expected = self.compose(spec, outcomes)
        require(certificate == expected["proof"], "Conjunction verdict or dependencies changed")
        return expected["status"]
