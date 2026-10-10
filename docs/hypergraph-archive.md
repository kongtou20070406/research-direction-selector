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

Archive inputs must be regular files. ZIP imports accept at most 1 GiB for the
whole container, 16 MiB for its central directory and 10,000 directory entries;
the original directory is checked before the ZIP parser loads its inventory.
Multipart and ZIP64 inventories are refused. The selected graph JSON remains
limited to 512 MiB, and `MANIFEST.json` to 8 MiB, with the selected original
bytes and SHA-256 checked against the manifest.

Ordinary zoom uses world-aligned spatial cells with a 12–28 screen-pixel
hysteresis band, revealing individual records as cells split. It does not replace
all records with a different semantic hierarchy at a global zoom threshold.
Research-group, source and block views remain explicit settings buttons. Opening
a semantic group fits its retained members' current bounds and scopes the view
to those exact descendants; a back button restores the previous view. Panning and zooming query spatial
indexes for the visible region, including links crossing the viewport. Original
nodes, edges, versions and hyperedge memberships remain in the complete data
layer. Aggregation is a display operation, never a merged scientific conclusion.

The page keeps the compact Obsidian-style canvas and controls. Search, display
levels, node and line sizes, and optional names are in the collapsed settings
panel. The same panel includes damping, attraction, weight-sensitive repulsion,
dependency flow, grouping and hyperedge clearance controls. Node categories have
editable colors, shapes and sizes. Relation families and individual recorded
relation types have editable colors, widths, line patterns and attraction,
repulsion or no-force behavior. Individual relations inherit family settings
until explicitly overridden. Settings are saved for the same archive identity.
Record types use distinct colors and shapes; hovering or clicking shows
their readable names and details. The overview includes at most 32 actual
representative records per visible research group, without inventing connections.
Representatives also share the overall 3,500-point drawing budget. Larger group
views are binned transparently; their counts describe groups at that zoom level.
The current display grows gradually on opening. Its duration and replay/skip
controls are available in settings; reset view returns to the overview without
changing the archived records. Labels retain a fixed screen font size through
zoom and node-size changes, avoid collisions and do not duplicate hover text.

Dense views retain nearby exact relations first and summarize remaining crossing
relations within the display budget of 3,500 lines. At most 128 summaries use
clipped representative original segments, without arrows or grid-centre nodes.
Each summary retains its represented relation count; node
details and agent queries still access the complete relations. Lines crossing
the visible area remain queryable even when both endpoints are outside it.

Raw record views aggregate nearby records into cells, with a maximum of 3,500
visible glyphs. Single-member cells keep the actual record's identity, color,
shape and status. Changed representations fade over 280 ms; exiting glyphs have
no labels or interaction, and the combined transition stays within that budget.
Screen-external
endpoints do not consume that record budget. At most 256 separate geometry anchors
support connection summaries; they have no glyph, label, interaction or physics.
Visible record identity, position, appearance and status are retained when they fit.
Aggregated points use their members' actual centroid and always have a positive
visible count. Their
counts account for every visible original record, including isolated records.
Search and paginated details still use the full identities and relationships.
The default spatial overview requests the raw spatial index once. Explicit
semantic views and representative dragging can use only the coarse indexes.
That first full record request can still take
longer on a large archive. Viewport requests are sent one at a time, with only the
latest pending camera view retained. Reused scenes avoid rebuilding unchanged
renderer topology, and raster labels are created only when needed.

Dragging a node reheats a bounded neighborhood with damping; dragging the
background pans the view. Dependency, source and history links use different
spring strengths and minimum lengths. Hidden membership links and relations set
to no force do not take spring or neighbor slots. Each new drag seed rebuilds its
own neighborhood, prioritizing configured active relation strength. Mixed bundles
retain independent force contributions rather than taking the first relation's
settings. The link-distance control scales the initial archived span, with 80
retaining the baseline. Springs preserve longer initial spans,
and weak position constraints prevent a distant source link from pulling the
released node across the archive. The local layout has at most 512 active nodes and
1,024 springs. This is a display approximation, not scientific analysis or an
all-edge physical simulation. During motion the existing scene follows the
layout. Camera requests during motion use an explicit stable indexed snapshot
with the current local motion overlaid, so panning and zooming remain responsive.
Exact updated crossing-link queries resume after the incident-edge index is
repaired in yielded batches. Pausing stops motion and commits that index repair.
Repeated pointer movements are coalesced to the newest position each frame;
release flushes that position before releasing the anchor. Clicking alone does
not start a force simulation. Relayout reheats a bounded visible neighborhood.
Orphan filtering uses the active display relationships and applies to all levels;
coarse member and relation counts are recomputed from the retained original records.
Scoping a group does not redefine whether a record is isolated: its external
active links remain clipped context geometry, while outside records are not drawn
as group members. Spatial cells also offer a paginated member list, so coincident
records remain accessible without inventing new positions. A selected record
keeps its own glyph and is deducted from the spatial-cell count.

Search the full archive to locate a node, then inspect its readable purpose and
paginated relationships. Scientific dependency, provenance, membership and
history retain their distinct meanings. A supported archival statement or a
high connection count does not establish experimental success or contribution
to a research objective. Successful/failed execution outlines require explicit
run or receipt outcomes; statement support is not execution success. Conflicting
or absent outcomes remain neutral. Original readable presentation and native
goal weights are retained when present; otherwise size/charge use connection
degree or uniform weighting. Missing scientific weight remains unmeasured.

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
## Readable sidecar input

Readable JSON sidecars retain their 8 MiB limit. The reader opens nonblocking
and requires a regular file before reading the same descriptor; FIFO and
device paths are refused before any JSON parse or wait for a writer.
