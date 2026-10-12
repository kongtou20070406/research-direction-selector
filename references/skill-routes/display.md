# Read-only graph display

Use only when the user requests a graph page or node explanation.

This procedure assumes the already-loaded root policy, `skill_dir`, `project_root`,
`CLI`, current original evidence and existing authorization. Reading this file
does not initialize state or authorize execution. Read only the applicable
section and its needed guide; reuse unchanged resources already read.

```text
    IF user asks to show/open a hypergraph or explain its nodes:
        LOAD docs/dashboard.md; use the user's actual project_root or explicit map
        VIEW := python -B "<skill_dir>/scripts/rds_hypergraph_view.py" --root "<project_root>"
        IF readable explanations are requested or existing record names lack meaning:
            RUN VIEW --export-agent-input; keep raw and readable separate
            INSPECT bound source/manifest/evidence; fill readable title/summary only
            SAVE readable JSON in the user's project; leave unknown meanings blank
            EXPORT with --readable-json <readable.json> --output <project_root>/dist/hypergraph.html
        ELSE: EXPORT with --output <project_root>/dist/hypergraph.html
        USE existing pinned Replica/Pixi assets when available; otherwise bundled Canvas
        READ actual export status/output; missing/corrupt graph is not a new demo
        OPEN the generated file through available host preview/browser tools
        RETURN the verified clickable file or actual local URL; never guess a localhost port
        KEEP graph display read-only; do not run research or change evidence status
        END this display route without initiating a research execution plan
```

## Operation guides

- [Graph export and readable interface](../../docs/dashboard.md)
