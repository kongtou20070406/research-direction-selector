# Presentation assets

Dashboard and hypergraph templates, scripts, CSS, license text and ordered
Replica patches live in `scripts/assets`: 11 runtime files and a directory-local
`.gitattributes`. This is resource relocation, not a demonstrated reduction in
application logic. Public constants and renderer interfaces remain available.

The loaders resolve assets beside their Python module using `__file__` and
decode raw bytes as UTF-8. Git text conversion is disabled for these resources;
leading/trailing newlines, CRLF and standalone CR must survive decoding.

Plugin builds and self-development snapshots select indexed resources
recursively. Stage new assets before building. Self-development binds these
resources as data. QUICK static Python discovery does not infer runtime asset
reads: bind every required resource explicitly with `--bind data=<path>`.
The 128-file and 64-MiB QUICK input limits are unchanged. A changed bound asset
must refuse reuse of an existing job; it must not silently rerender that job.

Focused regression checks:

```powershell
python -B -m unittest discover -s tests -p test_rds_presentation_assets.py -v
python -B -m unittest discover -s tests -p test_rds_dashboard.py -v
python -B -m unittest discover -s tests -p test_rds_hypergraph_view.py -v
python -B -m unittest discover -s tests -p test_rds_hypergraph_archive.py -v
python -B -m unittest discover -s tests -p test_rds_plugin_package.py -v
python -B -m unittest discover -s tests -p test_rds_self_development.py -v
```

Optional pinned Replica integration tests require `RDS_REPLICA_ROOT` and
`RDS_PIXI_JS` pointing to local dependencies matching the existing commit/hash
pins. The tests check escaping and actual worker behavior. The upstream Replica
sources are not redistributed here. Raw maintenance logs, local output pages
and development state belong in private evidence storage outside the checkout.
