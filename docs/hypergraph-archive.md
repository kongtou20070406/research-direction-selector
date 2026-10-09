# Browse a complete research archive

Export a complete archive with the existing Obsidian-style local renderer:

```powershell
python -B scripts/rds_hypergraph_view.py --root C:\research\project --archive complete-graph.zip --replica-root C:\tools\obsidian-graph-replica --pixi-js C:\tools\pixi-7.4.3.min.js --output dist\archive.html
```

`--archive` accepts a read-only archive JSON or the supplied ZIP containing
`ALL_NODES_AND_EDGES.json`. It does not extract or execute archive contents,
initialize a research project, or run scientific dependency analysis. The local
renderer and Pixi assets must match the existing pinned versions. The exported
HTML works offline; opening the page does not use a CDN.

Zoomed-out groups summarize their members and relations. Zooming in reveals
smaller groups and then individual nodes. Panning and zooming query spatial
indexes for the visible region, including links crossing the viewport. Original
nodes, edges, versions and hyperedge memberships remain in the complete data
layer. Aggregation is a display operation, never a merged scientific conclusion.

The page keeps the compact Obsidian-style canvas and controls. Search, display
levels, node and line sizes, and optional names are in the collapsed settings
panel. Record types use distinct colors and shapes; hovering or clicking shows
their readable names and details. The overview includes at most 32 actual
representative records per visible research group, without inventing connections.
Representatives also share the overall 3,500-point drawing budget. Larger group
views are binned transparently; their counts describe groups at that zoom level.
The slow reveal button replays the current display; reset view returns to the
overview without changing the archived records.

Dense views progressively coarsen connection bundles until they fit the display
budget of 3,500 lines. Every bundle retains its represented relation count; node
details and agent queries still access the complete relations. Lines crossing
the visible area remain queryable even when both endpoints are outside it.

Raw record views also aggregate when their drawing points exceed 3,500. Their
counts account for every visible original record, including isolated records.
Search and paginated details still use the full identities and relationships.
Only coarse spatial indexes are built at startup; the raw spatial index is built
on the first record viewport or record drag. That first request can still take
longer on a large archive. Viewport requests are sent one at a time, with only the
latest pending camera view retained. Reused scenes avoid rebuilding unchanged
renderer topology, and raster labels are created only when needed.

Dragging a node reheats a bounded neighborhood with damping; dragging the
background pans the view. Dependency, source and history links use different
spring strengths and minimum lengths. Springs preserve longer initial spans,
and weak position constraints prevent a distant source link from pulling the
released node across the archive. The local layout has at most 512 active nodes and
1,024 springs. This is a display approximation, not scientific analysis or an
all-edge physical simulation. During motion the existing scene follows the
layout; exact viewport queries resume after the incident-edge index is repaired
in small batches. Pausing stops motion and completes that index repair.

Search the full archive to locate a node, then inspect its readable purpose and
paginated relationships. Scientific dependency, provenance, membership and
history retain their distinct meanings. A supported archival statement or a
high connection count does not establish experimental success or contribution
to a research objective. Missing scientific weight remains unmeasured.

Agents can obtain separate readable and original information without opening a
browser:

```powershell
python -B scripts/rds_hypergraph_view.py --archive complete-graph.zip --export-agent-input --archive-node "original-node-id"
python -B scripts/rds_hypergraph_view.py --archive complete-graph.zip --export-agent-input --archive-edge "original-edge-id"
```

Each response binds the selected original object and its readable presentation
to the input graph. Omitting the node/edge selector exports the complete original
archive and readable layer, which can be large. The webpage presents readable
details; raw JSON, hashes and machine paths remain program-facing information.

The original `--hypergraph`, saved TMS and snapshot-bound `--readable-json`
routes continue to handle dependency maps with their existing validation and
scientific-analysis budgets. Archive browsing does not relax those budgets.
