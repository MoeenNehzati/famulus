# Graph sources

The repo diagrams are rendered with ELK from local structured specs.

- Source of truth: `graph_specs.py`
- Renderer: `render-graphs.py` calling `render_graph_with_elk.cjs`
- Vendored layout engine: `vendor/elk.bundled.js`
- Rendered output committed to the repo: `*.svg`
- Docs embed the SVG files directly
- Layout comes from ELK layered layout instead of hand-tuned Graphviz rank and invisible-edge tricks. The Python entrypoint validates the specs and asks the local Node renderer to build SVG with ELK.
- `vendor/elk.bundled.js` is vendored from upstream ELK and retains its EPL-2.0 license header.

Render after editing a graph source:

```bash
python3 graphs/render-graphs.py
```

Check that rendered images are current and graph style guards pass:

```bash
python3 graphs/render-graphs.py --check
```

Files:

- `runtime-map.svg` — plugin mode vs workstation mode.
- `daily-assistant-loop.svg` — remote data/state,
  skills that fetch or update it, and local scheduled automation.
- `research-writing.svg` — research skills grouped into
  structural, math-focused, and text/presentation review tools.
- `skill-development-framework.svg` —
  guidelines, blueprints, dispatcher, validators, pre-commit, CI, and hook
  tooling.
- `scaffolding-responsibility-map.svg`
  — how the authored surfaces and enforcement layers map to the end goals of
  the assistant-building convention.
- `skill-taxonomy.svg` — all live skills grouped by
  blueprint taxonomy; used as the mechanical coverage target so graph assets
  fail tests when the repo gains a new skill that the graph does not yet show.
