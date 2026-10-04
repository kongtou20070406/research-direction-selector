"""Replay closed rational obligations in an explicitly selected Lean 4 kernel.

This adapter is a mathematical subtask of RDS. It accepts neither user Lean
source nor tactics, and says nothing about a training run or model export.
Only installed native binaries are used; elan/download wrappers are never run.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading

from rds_verify_types import MAX_CERTIFICATE_BYTES, canonical, digest, rational, require

BACKEND = "lean4_closed_rational"
VERSION = 1
KINDS = {"lean_obligation", "lean_vector_obligation"}
SEMANTICS = "closed_Lean_Rat_relation"
VECTOR_SEMANTICS = "closed_Lean_Rat_vector_relations"
TIMEOUT_SECONDS = 3
VECTOR_TIMEOUT_SECONDS = 10
MAX_OUTPUT_BYTES = 65536
THEOREM = "RDS.obligation"
AXIOM_AUDIT = "'RDS.obligation' does not depend on any axioms"
RELATIONS = {"eq": "=", "lt": "<", "le": "≤"}
FALLBACK_BACKEND = "rds_python_closed_rational"
FORMAL_ROOT = Path(__file__).resolve().parents[1] / "formal"


class NoNativeLean(ValueError):
    """No installed toolchain; unlike invalid explicit configuration, may fall back."""


def render_source(spec):
    """Render only a fixed template with validated reduced rational literals."""
    require(isinstance(spec, dict), "Specification must be an object")
    require(len(canonical(spec).encode("utf-8")) <= MAX_CERTIFICATE_BYTES,
            "Specification exceeds size limit")
    require(type(spec["schema"]) is int and spec["schema"] == 1, "Unsupported specification schema")
    require(isinstance(spec.get("kind"), str) and spec["kind"] in KINDS,
            "Unsupported Lean obligation kind")
    def literal(value):
        # mkRat/division have normalization lemmas or opaque arithmetic. The
        # reduced constructor lets the kernel check both invariants by decide.
        return (f"(Rat.mk' ({value.numerator} : Int) {value.denominator} "
                "(by decide) (by decide))")

    if spec["kind"] == "lean_obligation":
        require(set(spec) == {"schema", "kind", "relation", "left", "right"},
                "Lean obligation requires schema, kind, relation, left and right")
        require(isinstance(spec["relation"], str) and spec["relation"] in RELATIONS,
                "Supported Lean rational relations are eq, lt and le")
        left, right = rational(spec["left"]), rational(spec["right"])
        proposition = f"{literal(left)} {RELATIONS[spec['relation']]} {literal(right)}"
    else:
        require(spec["kind"] == "lean_vector_obligation" and
                set(spec) == {"schema", "kind", "relations"},
                "Vector Lean obligation requires a relations list")
        relations = spec["relations"]
        require(isinstance(relations, list) and 1 <= len(relations) <= 32,
                "Vector Lean obligations require 1..32 rational relations")
        propositions = []
        for relation in relations:
            require(isinstance(relation, dict) and
                    set(relation) == {"relation", "left", "right"} and
                    isinstance(relation["relation"], str) and relation["relation"] in RELATIONS,
                    "Each vector relation requires a supported relation and two rationals")
            left, right = rational(relation["left"]), rational(relation["right"])
            propositions.append(f"({literal(left)} {RELATIONS[relation['relation']]} {literal(right)})")
        proposition = " /\\ ".join(propositions)
    return ("import Init.Data.Rat.Basic\n"
            "set_option maxHeartbeats 100000\n"
            "set_option maxRecDepth 512\n"
            "namespace RDS\n"
            f"theorem obligation : {proposition} := by decide\n"
            "end RDS\n"
            "#print axioms RDS.obligation\n")


def _executable():
    if "RDS_LEAN_EXECUTABLE" in os.environ:
        raw = os.environ["RDS_LEAN_EXECUTABLE"]
        require(raw, "RDS_LEAN_EXECUTABLE must select an existing native Lean 4 binary")
        candidates = [Path(raw)]
    else:
        suffix = ".exe" if os.name == "nt" else ""
        toolchains = Path.home() / ".elan" / "toolchains"
        candidates = []
        toolchain = FORMAL_ROOT / "lean-toolchain"
        if toolchain.is_file():
            name = toolchain.read_text(encoding="utf-8").strip().replace("/", "--").replace(":", "---")
            candidates.append(toolchains / name / "bin" / ("lean" + suffix))
        for command in ("lean", "lake"):
            found = shutil.which(command)
            if found:
                candidates.append(Path(found).with_name("lean" + suffix))
        candidates.extend(sorted(toolchains.glob("*/bin/lean" + suffix), reverse=True))
        candidates = [path for path in candidates if path.is_file() and
                      path.resolve().parent != (Path.home() / ".elan" / "bin").resolve()]
        if not candidates:
            raise NoNativeLean("No installed native Lean 4 toolchain; automatic downloads are disabled")
    path = candidates[0]
    require(path.is_absolute() and path.is_file(), "Lean executable must be an existing absolute path")
    path = path.resolve()
    # Explicitly reject the usual automatic-download shims as well as elan.
    require(path.name.lower() not in {"elan", "elan.exe"}, "Elan download wrappers are unsupported")
    require(path.parent != (Path.home() / ".elan" / "bin").resolve(),
            "Select the native toolchain binary rather than an elan shim")
    with path.open("rb") as stream:
        fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    return path, fingerprint


def _run(command, cwd, lean_path=None, timeout_seconds=None):
    """Enforce time and output budgets while draining the child pipe."""
    env = os.environ.copy()
    for key in ("LEAN_PATH", "LEAN_SRC_PATH", "LEAN_SYSROOT"):
        env.pop(key, None)
    if lean_path is not None:
        env["LEAN_PATH"] = os.pathsep.join(str(path) for path in lean_path)
    timeout = TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               shell=False, env=env)
    output = bytearray()
    overflow = threading.Event()
    reader_failed = threading.Event()

    def drain():
        try:
            while block := process.stdout.read(4096):
                if len(output) + len(block) > MAX_OUTPUT_BYTES:
                    overflow.set()
                    process.kill()
                    return
                output.extend(block)
        except OSError:
            reader_failed.set()
        finally:
            process.stdout.close()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise ValueError(f"Lean check exceeded the {timeout}-second time budget") from None
    finally:
        reader.join(timeout=1)
    require(not reader.is_alive(), "Lean output reader did not finish within its budget")
    require(not reader_failed.is_set(), "Lean output could not be read completely")
    require(not overflow.is_set(), "Lean output exceeded the 64-KiB limit")
    return process.returncode, bytes(output).decode("utf-8")


def _native_check(source, timeout_seconds=None):
    executable, fingerprint = _executable()
    with tempfile.TemporaryDirectory(prefix="rds-lean-") as folder:
        code, version = _run([str(executable), "--version"], folder,
                             timeout_seconds=timeout_seconds)
        require(code == 0 and re.fullmatch(r"Lean \(version 4\.[0-9]+\.[^\r\n]+\)\s*", version),
                "Executable did not identify itself as Lean 4")
        path = Path(folder) / "Obligation.lean"
        path.write_text(source, encoding="utf-8")
        code, stdout = _run([str(executable), "--trust=0", "--memory=512", "--threads=1", str(path)],
                            folder, timeout_seconds=timeout_seconds)
    require(code == 0, "Lean did not prove this closed rational obligation")
    require(stdout.strip() == AXIOM_AUDIT,
            "Lean proof is missing its axiom-free theorem audit")
    return {"lean_version": version.strip(), "lean_executable_sha256": fingerprint,
            "stdout": stdout}


def _certificate(spec, source, checked):
    return {"schema": 1, "backend": BACKEND, "version": VERSION,
            "verdict": "PASS", "spec_sha256": digest(spec),
            "source_sha256": digest(source.encode("utf-8")), "source": source,
            "lean_version": checked["lean_version"],
            "lean_executable_sha256": checked["lean_executable_sha256"],
            "theorem": THEOREM, "axioms": [],
            "semantics": VECTOR_SEMANTICS if spec["kind"] == "lean_vector_obligation" else SEMANTICS,
            "assurance": "LEAN_KERNEL_CHECKED"}


def _fallback_certificate(spec):
    # The same strict renderer validates the entire declaration before Fraction
    # replay. False closed relations remain UNKNOWN, not scientific refutations.
    render_source(spec)
    if spec["kind"] == "lean_obligation":
        left, right = rational(spec["left"]), rational(spec["right"])
        require({"eq": left == right, "lt": left < right, "le": left <= right}[spec["relation"]],
                "Closed rational relation is false")
        return {"schema": 1, "backend": FALLBACK_BACKEND, "version": VERSION,
                "verdict": "PASS", "spec_sha256": digest(spec),
                "left": str(left), "right": str(right), "relation": spec["relation"],
                "assurance": "CERTIFICATE_CHECKED", "semantics": "closed_exact_rational_relation"}
    relations = []
    for relation in spec["relations"]:
        left, right = rational(relation["left"]), rational(relation["right"])
        require({"eq": left == right, "lt": left < right, "le": left <= right}[relation["relation"]],
                "Closed rational vector relation is false")
        relations.append({"left": str(left), "right": str(right), "relation": relation["relation"]})
    return {"schema": 1, "backend": FALLBACK_BACKEND, "version": VERSION,
            "verdict": "PASS", "spec_sha256": digest(spec), "relations": relations,
            "assurance": "CERTIFICATE_CHECKED", "semantics": "closed_exact_rational_vector_relations"}


def verify_rational(spec):
    """Check a closed rational relation with exact Python arithmetic only."""
    try:
        certificate = _fallback_certificate(spec)
        return {"status": "PASS", "assurance": "CERTIFICATE_CHECKED",
                "backend": FALLBACK_BACKEND, "semantics": certificate["semantics"],
                "certificate": certificate}
    except (ValueError, TypeError, OSError, ZeroDivisionError, OverflowError, RecursionError, UnicodeError) as exc:
        return {"status": "UNKNOWN", "assurance": "NONE", "backend": FALLBACK_BACKEND,
                "semantics": "closed_exact_rational_relation", "certificate": None, "reason": str(exc)}


def verify(spec, *, allow_fallback=True):
    """Check a generated Lean proof, optionally falling back when Lean is absent."""
    try:
        source = render_source(spec)
        try:
            if spec["kind"] == "lean_vector_obligation":
                checked = _native_check(source, timeout_seconds=VECTOR_TIMEOUT_SECONDS)
            else:
                checked = _native_check(source)
        except NoNativeLean as exc:
            if not allow_fallback:
                raise
            result = verify_rational(spec)
            if result["status"] == "PASS":
                result["reason"] = str(exc) + "; exact rational replay only"
            return result
        certificate = _certificate(spec, source, checked)
        return {"status": "PASS", "assurance": "LEAN_KERNEL_CHECKED",
                "backend": BACKEND, "semantics": SEMANTICS, "certificate": certificate,
                "artifact": {"source": source, **checked},
                "reason": "Generated closed rational theorem was checked with no axioms"}
    except (ValueError, TypeError, OSError, ZeroDivisionError, OverflowError, RecursionError, UnicodeError) as exc:
        return {"status": "UNKNOWN", "assurance": "NONE", "backend": BACKEND,
                "semantics": SEMANTICS, "certificate": None, "reason": str(exc)}


def check_certificate(spec, certificate):
    """Re-render and replay Lean; supplied proof text/output cannot certify itself."""
    try:
        require(isinstance(certificate, dict), "Certificate must be an object")
        require(len(canonical(certificate).encode("utf-8")) <= MAX_CERTIFICATE_BYTES,
                "Certificate exceeds size limit")
        require(type(certificate.get("schema")) is int and certificate["schema"] == 1 and
                type(certificate.get("version")) is int and certificate["version"] == VERSION,
                "Unsupported certificate schema or version")
        if certificate.get("backend") == FALLBACK_BACKEND:
            return certificate == _fallback_certificate(spec)
        source = render_source(spec)
        require(certificate.get("source") == source and
                certificate.get("source_sha256") == digest(source.encode("utf-8")) and
                certificate.get("spec_sha256") == digest(spec), "Certificate binding mismatch")
        if spec["kind"] == "lean_vector_obligation":
            checked = _native_check(source, timeout_seconds=VECTOR_TIMEOUT_SECONDS)
        else:
            checked = _native_check(source)
        return certificate == _certificate(spec, source, checked)
    except (ValueError, TypeError, OSError, ZeroDivisionError, OverflowError, RecursionError, UnicodeError):
        return False
