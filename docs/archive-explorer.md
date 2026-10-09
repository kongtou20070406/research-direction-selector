# Read-only research archive explorer

Use this separate display route when a complete archive is too large to read as
one flat hypergraph. It does not run dependency analysis, submit a new TMS map,
change a ledger, or alter the existing hypergraph analyzer and execution limits.

```sh
python -B scripts/rds_archive_view.py --archive archive.json.gz --output archive.html
```

The input is JSON, optionally gzip-compressed, with `nodes`, `edges`, and optional
`groups`. Node and edge IDs must be unique strings. Every edge endpoint must exist.
Parallel edges with different IDs are retained. Groups are a display index; they
do not add scientific edges or promote evidence status.
An optional finite numeric `priority` orders navigation; it does not change
record status, dependency meaning, or which records remain accessible.

```json
{
  "title": "Research archive",
  "nodes": [
    {"id": "experiment:one", "label": "Experiment one", "type": "experiment",
     "status": "UNKNOWN", "group": "method-a", "primary": true,
     "summary": "Original scoped observation."},
    {"id": "receipt:one", "label": "Original receipt", "group": "method-a",
     "raw_ref": {"path": "evidence/receipt.json", "sha256": "original-digest"}}
  ],
  "edges": [{"id": "binding:one", "source": "experiment:one", "target": "receipt:one",
             "label": "Original evidence binding", "semantic": "provenance"}],
  "groups": [{"id": "method-a", "label": "Method A", "summary": "Research scope."}]
}
```

The first screen is a constellation of groups. Cross-group lines aggregate actual
source edges, and group counts describe the display index, not training counts.
Click a group for its `primary` records, then a record for its direct neighborhood
and source references. All group records remain reachable through the group toggle
and global search. Breadcrumbs return to the group or overview. Search, overview,
neighborhoods, and relationship lists paginate rather than silently dropping
records. Visible and total counts distinguish presentation from source coverage.

The optional full view batches every source edge and node on one Canvas. It is a
coverage overview, not a claim that hundreds of thousands of links are readable
at once. It supports pan and zoom; record inspection remains available through
search and the grouped views. Source metadata is available through each record.
Only safe relative evidence links inside the HTML's directory are clickable.
All labels and summaries enter the DOM as text; the offline payload is compressed
and base64-encoded. There are no runtime network requests.

There is no fixed node, edge, or input-byte bound on this archival display route.
This is not infinite physical capacity: export and initial decompression/indexing
currently need memory proportional to the complete graph. The page reports a
load failure instead of calling partial data complete. Chromium and Firefox must
support `DecompressionStream`. Chunked loading and out-of-core storage are future
work; this implementation does not claim them. The underlying source graph and
edge multiplicity are unchanged by all display aggregation.

Validation uses `python -m unittest discover -s tests -p test_archive_view.py`,
including inputs beyond the former viewer bounds, parallel edges, isolated nodes,
identity errors, hostile text, gzip input, and actual read-only CLI export.
