"""Record real exports before extraction and compare exact bytes afterwards."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
LOCAL = EVIDENCE / "_local"
sys.path.insert(0, str(ROOT / "scripts"))
import rds_dashboard as dashboard
import rds_hypergraph_view as viewer
from rds_hypergraph_archive import read_archive, render_archive_html


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 11, tzinfo=timezone.utc).astimezone(tz)


def identity(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def pinned_inputs():
    upstream = LOCAL / "upstream"
    paths = {}
    for name, expected in viewer.REPLICA_FILES.items():
        paths[upstream / "replica" / name] = (
            "https://raw.githubusercontent.com/runningZ1/obsidian-graph-replica/"
            + viewer.REPLICA_COMMIT + "/replica/" + name, expected, True)
    pixi = upstream / "pixi.min.js"
    paths[pixi] = ("https://registry.npmjs.org/pixi.js/-/pixi.js-7.4.3.tgz",
                   viewer.PIXI_SHA256, False)
    for path, (url, expected, normalize) in paths.items():
        if not path.exists():
            with urlopen(url, timeout=30) as response:
                raw = response.read()
            if path == pixi:
                import io
                import tarfile
                with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
                    raw = archive.extractfile("package/dist/pixi.min.js").read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        raw = path.read_bytes()
        assert identity(raw.replace(b"\r\n", b"\n") if normalize else raw)["sha256"] == expected
    return upstream, pixi


def exports(destination, upstream, pixi):
    destination.mkdir(parents=True, exist_ok=True)
    records = {}

    def save(name, page):
        output = destination / (name + ".html")
        viewer.write_html_atomic(output, page)
        records[name] = identity(output.read_bytes())

    with patch.object(dashboard, "datetime", FixedClock):
        snapshot = dashboard.demo_snapshot(ROOT)
        save("dashboard-demo", dashboard.render_html(snapshot))
        hostile = deepcopy(snapshot)
        hostile["state"]["contract"]["claim"] = (
            '</script><script>alert("owned")</script>&\u2028\u2029__RDS_SNAPSHOT__')
        save("dashboard-hostile", dashboard.render_html(hostile, {"diagnosis": hostile["state"]["contract"]["claim"]}))
        output = destination / "dashboard-cli.html"
        dashboard.main(["--root", str(ROOT), "--demo", "--output", str(output)])
        records["dashboard-cli"] = identity(output.read_bytes())
    spec = json.loads((ROOT / "examples/hypergraph-view.json").read_text(encoding="utf-8"))
    small = viewer.graph_view(spec, "fixture:assets", demo=True)
    large = viewer.graph_view(viewer.large_demo(), "synthetic:assets", demo=True)
    hostile = deepcopy(spec)
    hostile["nodes"][0]["label"] = (
        '</script>&\u2028\u2029__GRAPH_CSS____GRAPH_JS____D3_BUNDLE____GRAPH_DATA__'
        '__STYLE____PIXI____D3____WORKER____RENDERER____APP____DATA__')
    hostile = viewer.graph_view(hostile, "fixture:hostile")
    for name, result in (("small", small), ("large", large), ("hostile", hostile)):
        save("hypergraph-" + name, viewer.render_html(result))
        save("replica-" + name, viewer.render_replica_html(result, upstream, pixi))
    for suffix, tail in (("demo", []), ("large", ["--large"])):
        output = destination / ("hypergraph-cli-" + suffix + ".html")
        viewer.main(["--root", str(ROOT), "--demo", "--output", str(output), *tail])
        records["hypergraph-cli-" + suffix] = identity(output.read_bytes())
    archive_input = LOCAL / "archive.json"
    archive_input.write_bytes((json.dumps(spec, ensure_ascii=False) + "\n").encode("utf-8"))
    save("archive", render_archive_html(read_archive(archive_input), upstream, pixi))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    args = parser.parse_args()
    upstream, pixi = pinned_inputs()
    records = exports(LOCAL / args.phase, upstream, pixi)
    report = {"phase": args.phase, "python": sys.version, "root": str(ROOT),
              "clock": "2026-10-11T00:00:00+00:00; input clock fixed, output not normalized",
              "sources": {name: identity((ROOT / "scripts" / name).read_bytes())
                          for name in ("rds_dashboard.py", "rds_hypergraph_view.py")},
              "replica_commit": viewer.REPLICA_COMMIT, "pixi_sha256": viewer.PIXI_SHA256,
              "exports": records}
    if args.phase == "after":
        baseline = json.loads((EVIDENCE / "exports-before.json").read_text(encoding="utf-8"))
        report["comparisons"] = {}
        for name, record in records.items():
            before = (LOCAL / "before" / (name + ".html")).read_bytes()
            after = (LOCAL / "after" / (name + ".html")).read_bytes()
            report["comparisons"][name] = {"before": baseline["exports"][name], "after": record,
                                           "byte_equal": before == after}
        assert all(row["byte_equal"] for row in report["comparisons"].values())
    (EVIDENCE / ("exports-" + args.phase + ".json")).write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
