"""Frozen maintenance prevalidation and kernel-only ledger/file rechecks."""
from dataclasses import dataclass

from rds_admission_read import PreparedAdmission, StaleAdmission, check_locked, prepare
from rds_project_values import canonical, digest, require
from rds_source_documents import strict_json


@dataclass(frozen=True, slots=True)
class PreparedMaintenance:
    schema_version: int
    contract_sha256: str
    review_json: str | None
    rejection: str | None
    fence: PreparedAdmission


def freeze(db, ledger_path, spec, contract, review, rejection, files):
    require((review is None) != (rejection is None), 'Maintenance needs one review or rejection')
    return PreparedMaintenance(1, digest(contract), canonical(review) if review is not None else None,
                               rejection, prepare(db, ledger_path, spec, files, None))


def check_review(store, db, contract, spec, packet):
    require(type(packet) is PreparedMaintenance and packet.schema_version == 1,
            'Native maintenance prevalidation is missing; user JSON is not admission')
    if packet.contract_sha256 != digest(contract):
        raise StaleAdmission('Maintenance contract changed after preparation; STALE')
    check_locked(db, store.path, store.root, spec, packet.fence)
    # Raise in the original caller's failure/settlement boundary, not during
    # preparation where a queued attempt might otherwise lose its receipt.
    if packet.rejection is not None:
        raise ValueError(packet.rejection)
    require(packet.review_json is not None, 'Maintenance review is missing')
    return strict_json(packet.review_json)
