"""Read-only session inspection for RDS, with no storage side effects.

`list` and `observe` answer the operator's question "is RDS on in this session?"
without creating a directory, database, auxiliary file or usage record. That
property is the whole point: a self-check that writes usage cannot be used to
verify that nothing is being written (plan sections 6.2, 6.6, 6.7).

`enable` and `disable` are deliberately fail-closed. A session may only be
enabled by an authenticated operator confirmation issued by a Session Authority
that lives outside the model process (plan section 4.2). No such authority is
installed in this checkout, and a local marker file or environment variable is
explicitly not an enable record (plan sections 4.1, 6.4). So these verbs report
that no confirmation was obtained and leave admission OFF. They write nothing.
"""
import argparse
import json
from pathlib import Path
import sys

SCHEMA = 1
SKILL_NAME = "research-direction-selector"


def _package_root():
    """Locate the packaged Skill beside this script, if this copy has one."""
    return Path(__file__).resolve().parents[1]


def installation():
    """Report what this copy can actually observe about its own packaging.

    A host's install manifest is the trusted source in the plan, and this
    process cannot read it, so the host registration stays UNKNOWN. The
    colocated Skill file only proves the files are present.
    """
    root = _package_root()
    package_present = (root / "SKILL.md").is_file()
    return {
        "installation": "INSTALLED" if package_present else "UNKNOWN",
        "installation_source": "colocated-skill-file" if package_present else "UNKNOWN",
        "host_registration": "UNKNOWN",
        "package_root": str(root) if package_present else None,
    }


def observe():
    """Return the switch state that the available records actually support.

    Every field is either read from a record that exists or explicitly UNKNOWN.
    Nothing here is inferred from the agent's text, from a shell variable, from
    a directory existing, or from the fact that this command ran.
    """
    facts = installation()
    installed = facts["installation"] == "INSTALLED"
    return {
        "schema": SCHEMA,
        "installation": facts["installation"],
        "installation_source": facts["installation_source"],
        "host_registration": facts["host_registration"],
        # No Session Authority is reachable, so no epoch can be validated and
        # no enable record can exist. Installed software that nobody enabled is
        # INSTALLED_OFF; unknown software is not a switch state at all.
        "switch_state": "INSTALLED_OFF" if installed else "UNKNOWN",
        "new_admission": "OFF",
        "scope_id": None,
        "enable_epoch": 0,
        "mode": None,
        "authority_status": "UNKNOWN",
        "authority_present": False,
        "operator_attested": "NOT_APPLICABLE",
        "skill_load": "UNKNOWN",
        # Plan section 6.4 requires this to be stated, not smoothed over: this
        # checkout has no host-side per-session gate, so a host that loads Skill
        # text globally keeps loading it. Being unable to prevent that is the
        # honest reading, and it is not a pass for "default off".
        "skill_auto_application": "UNKNOWN_NOT_PREVENTED",
        "coverage": "NOT_APPLICABLE",
        "usage_policy": "OFF",
        "usage_health": "NOT_PROBED",
        "active_action_ids": [],
        "last_authority_sequence": 0,
        "limitations": [
            "No Session Authority is installed, so enable cannot be confirmed and admission stays OFF.",
            "Host registration and Skill loading are UNKNOWN because no trusted host manifest was read.",
            "Skill auto-application is UNKNOWN_NOT_PREVENTED: this build has no host-side per-session gate, so an automatically applied Skill body is not stopped here. Shortening the Skill description is not evidence that it is (plan section 6.4).",
            "Usage health is NOT_PROBED because no usage database was opened; this is a deliberate absence of probing, not a claim that usage is writable.",
            "This observation created no directory, database, log or usage record.",
        ],
    }


def enable(*, host, instance, session, root, mode):
    """Refuse to enable without an authenticated operator confirmation."""
    missing = [name for name, value in (("--host", host), ("--instance", instance),
                                        ("--session", session), ("--root", root))
               if not value]
    if missing:
        raise ValueError("Missing required session locator(s): " + ", ".join(missing))
    if mode not in {"observe", "research"}:
        raise ValueError("--mode must be observe or research")
    return {
        "schema": SCHEMA,
        "requested": "enable",
        "enable_result": "PENDING",
        "operator_attested": "UNKNOWN",
        "switch_state": "INSTALLED_OFF",
        "new_admission": "OFF",
        "scope_id": None,
        "enable_epoch": 0,
        "mode": None,
        "reason": "No Session Authority is installed to authenticate the operator and issue an enable epoch.",
        "wrote": False,
    }


def disable(*, host, instance, session):
    """Report that no epoch was revoked because none was ever granted."""
    missing = [name for name, value in (("--host", host), ("--instance", instance),
                                        ("--session", session)) if not value]
    if missing:
        raise ValueError("Missing required session locator(s): " + ", ".join(missing))
    return {
        "schema": SCHEMA,
        "requested": "disable",
        "disable_result": "NO_OP",
        "switch_state": "INSTALLED_OFF",
        "new_admission": "OFF",
        "active_action_ids": [],
        "reason": "No enable epoch exists for this scope, so there is nothing to revoke.",
        "wrote": False,
    }


def render(report):
    """Human-readable form for section 6.7: one obvious line first."""
    if report.get("requested") in {"enable", "disable"}:
        result = report.get(report["requested"] + "_result")
        lines = [f"RDS: {report['requested']} request -> {result}",
                 "switch state: " + str(report.get("switch_state")),
                 "new admission: " + str(report.get("new_admission")),
                 "reason: " + str(report.get("reason")),
                 "this request wrote nothing"]
        if report["requested"] == "enable":
            lines.insert(3, "operator attested: " + str(report.get("operator_attested")))
        return "\n".join(lines)
    state = report.get("switch_state", "UNKNOWN")
    if state == "INSTALLED_OFF":
        head = "RDS: installed, NOT enabled for this session (default off)"
    elif state == "ENABLED":
        head = "RDS: ENABLED for this session (mode=" + str(report.get("mode")) + ")"
    elif state == "EXPLICIT_OFF":
        head = "RDS: explicitly disabled for this session"
    elif state == "NOT_INSTALLED":
        head = "RDS: not installed"
    else:
        head = "RDS: switch state UNKNOWN"
    lines = [head,
             "new admission: " + str(report.get("new_admission")),
             "authority: " + str(report.get("authority_status")),
             "skill auto-apply: " + str(report.get("skill_auto_application")),
             "usage policy: " + str(report.get("usage_policy"))]
    if report.get("reason"):
        lines.append("reason: " + str(report["reason"]))
    lines.append("this observation wrote nothing")
    return "\n".join(lines)


def parser():
    # --json is accepted both before and after the verb so that neither form
    # silently becomes a positional argument.
    entry = argparse.ArgumentParser(prog="rds_session.py", add_help=True,
                                    description="Read-only RDS session inspection; observation never writes.")
    entry.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    # SUPPRESS keeps the verb's parser from overwriting a --json that appeared
    # before the verb. This is the standard argparse parent/child trap.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="Emit JSON instead of text.")
    verbs = entry.add_subparsers(dest="verb")
    verbs.add_parser("list", parents=[common], help="List session scopes and switch states.")
    verbs.add_parser("observe", parents=[common], help="Observe this session's switch state.")
    for verb, needs_mode in (("enable", True), ("disable", False)):
        action = verbs.add_parser(verb, parents=[common],
                                  help="Request " + verb + " through the operator control plane.")
        action.add_argument("--host", default=None)
        action.add_argument("--instance", default=None)
        action.add_argument("--session", default=None)
        action.add_argument("--root", default=None)
        action.add_argument("--confirm-ui", action="store_true",
                            help="Request the operator confirmation flow; this is not a credential.")
        if needs_mode:
            action.add_argument("--mode", default=None)
    for verb in ("list", "observe"):
        verbs.choices[verb].add_argument("--host", default=None)
        verbs.choices[verb].add_argument("--instance", default=None)
        verbs.choices[verb].add_argument("--session", default=None)
    return entry


def main(argv=None):
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.verb in {None, "list"}:
        facts = observe()
        # A scope list and the switch reading must not disagree, so the list
        # carries the same observed facts rather than a second guess.
        report = {"schema": SCHEMA, "requested": "list", "sessions": [], "wrote": False,
                  "reason": "No Session Authority is installed, so no session scope is recorded.",
                  **facts}
    elif args.verb == "observe":
        report = {"schema": SCHEMA, "requested": "observe", "wrote": False, **observe()}
    elif args.verb == "enable":
        report = enable(host=args.host, instance=args.instance, session=args.session,
                        root=args.root, mode=args.mode)
    else:
        report = disable(host=args.host, instance=args.instance, session=args.session)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print("[RDS-REJECT] " + str(exc), file=sys.stderr)
        raise SystemExit(1)
