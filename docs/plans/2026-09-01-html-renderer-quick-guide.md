# HTML Renderer Quick Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional, renderer-owned Quick guide that teaches the standalone HTML graph viewer and is enabled for math-dependency graphs.

**Architecture:** `ElkHtmlRenderer` owns an immutable `QuickGuide` definition and an instance-level `quick_guide_enabled` flag. The shared HTML runtime renders that definition without changing graph payloads or viewer behavior, while a small math renderer subclass adjusts only domain-specific wording and opts in at the existing math build boundary.

**Tech Stack:** Python 3.11+, dataclasses, standalone HTML/CSS/JavaScript, ELK viewer runtime, pytest/unittest, headless Chrome browser tests.

**Spec:** Approved in-chat design from 2026-09-01; this bounded change has no separate spec document.

## Global Constraints

- Call the feature **Quick guide** everywhere in reader-facing text.
- `ElkHtmlRenderer` defaults the guide to disabled, preserving every existing caller's generated behavior.
- `ElkHtmlRenderer(quick_guide_enabled=True)` enables its `quick_guide` property; subclasses may override that property and reuse `super().quick_guide`.
- The math-dependency renderer opts in; other renderer consumers remain unchanged.
- Guide dismissal is stored per graph at `${viewerStateKey}::quick-guide`; it must not enter or reset the existing viewer-state payload.
- Opening, advancing, finishing, or skipping the guide must not change graph selection, visibility, filters, layout, pan/zoom, sidebar state, or canonical JSON.
- Reuse existing toolbar, canvas, sidebars, DOM ids, `viewerStateKey`, and “How to use” help. Do not add a tour dependency, a payload-schema field, telemetry, animations, or a general plugin system.
- Execute this plan in an isolated clean worktree created with `superpowers:using-git-worktrees`; the current checkout has pre-existing edits in `page.html`, `viewer.css`, and `README.md` that must not be mixed with this feature.
- Do not commit unless the user explicitly authorizes it. Commit commands below are optional checkpoints to run only after that authorization.
- Before every authorized commit, stage only the paths named by that task, then run `git diff --cached --check`, inspect `git diff --cached --name-only` for exact task paths, and inspect the complete `git diff --cached` for exact owned hunks. After the commit and its hooks, run `git diff HEAD^ HEAD --check`, inspect `git diff --name-only HEAD^ HEAD`, and inspect the complete `git diff HEAD^ HEAD`; report and repair any hook-added or unrelated path before continuing.

---

### Task 1: Renderer-owned guide contract

**Files:**
- Modify: `src/officina/visualization/elk_html_renderer.py:74-185`
- Modify: `src/officina/visualization/html_renderer/runtime/bootstrap.js:1-15`
- Modify: `tests/test_visualization_renderer_cli.py:1-73`

**Interfaces:**
- Produces: `QuickGuideStep(target: str, title: str, body: str)` as a frozen dataclass.
- Produces: `QuickGuide(title: str, steps: tuple[QuickGuideStep, ...])` as a frozen dataclass.
- Produces: `ElkHtmlRenderer(*, quick_guide_enabled: bool = False, validator: PayloadValidator | None = None, graph: Graph | None = None)`.
- Produces: overridable `ElkHtmlRenderer.quick_guide -> QuickGuide | None`.
- Produces: serialized browser constant `QUICK_GUIDE_CONFIG`, either `null` or `{"title": ..., "steps": [...]}`.

- [ ] **Step 1: Write failing renderer contract tests**

Add a minimal valid graph fixture and tests that assert: the default renderer serializes `null`; an enabled renderer serializes the generic guide; a subclass can replace one step while reusing the remaining `super().quick_guide.steps`; and enabling a subclass whose property returns `None` raises `ValueError("quick guide is enabled but no guide is defined")`.

```python
from dataclasses import replace

import pytest

from officina.visualization.elk_html_renderer import ElkHtmlRenderer, QuickGuide


MINIMAL_GRAPH = {
    "schema_version": 2,
    "graph_id": "quick-guide-contract",
    "entities": [{
        "id": "result", "type": "theorem", "short_title": "Result",
        "position": 0, "connects_to": [],
    }],
}


def test_quick_guide_is_disabled_by_default() -> None:
    html = ElkHtmlRenderer().render_graph_html(MINIMAL_GRAPH)
    assert "const QUICK_GUIDE_CONFIG = null;" in html


def test_subclass_can_adjust_inherited_quick_guide() -> None:
    class TeachingRenderer(ElkHtmlRenderer):
        @property
        def quick_guide(self) -> QuickGuide:
            guide = super().quick_guide
            assert guide is not None
            first = replace(guide.steps[0], body="Read this graph from left to right.")
            return replace(guide, steps=(first, *guide.steps[1:]))

    html = TeachingRenderer(quick_guide_enabled=True).render_graph_html(MINIMAL_GRAPH)
    assert '"body": "Read this graph from left to right."' in html


def test_enabled_renderer_requires_a_guide_definition() -> None:
    class NoGuideRenderer(ElkHtmlRenderer):
        @property
        def quick_guide(self) -> None:
            return None

    with pytest.raises(ValueError, match="quick guide is enabled but no guide is defined"):
        NoGuideRenderer(quick_guide_enabled=True).render_graph_html(MINIMAL_GRAPH)
```

- [ ] **Step 2: Run the contract tests and verify they fail**

Run: `pytest tests/test_visualization_renderer_cli.py -q`

Expected: FAIL because `QuickGuide`, the constructor flag, and `QUICK_GUIDE_CONFIG` do not exist.

- [ ] **Step 3: Add the minimal immutable renderer contract**

Define the dataclasses and generic guide beside `ElkHtmlRenderer`. Keep the concise guide anchored only to stable, existing elements; its final step points readers to the existing detailed help instead of duplicating that help.

```python
@dataclass(frozen=True, slots=True)
class QuickGuideStep:
    target: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class QuickGuide:
    title: str
    steps: tuple[QuickGuideStep, ...]


class ElkHtmlRenderer(BaseRenderer):
    def __init__(self, *, quick_guide_enabled: bool = False, validator=None, graph=None):
        super().__init__(validator=validator, graph=graph)
        self.quick_guide_enabled = quick_guide_enabled

    @property
    def quick_guide(self) -> QuickGuide:
        return QuickGuide(title="Quick guide", steps=(
            QuickGuideStep("#canvas-wrap", "Explore the graph", "Click a node or edge to inspect it. Scroll to zoom and drag empty space to pan."),
            QuickGuideStep("#left-panel-toggle", "Inspector", "This toggle shows or hides the Inspector, which explains the current selection and restores individually hidden nodes."),
            QuickGuideStep("#canvas-toolbar", "Act on the graph", "Use the toolbar to undo, hide or dim a selection, reset the view, and control zoom."),
            QuickGuideStep("#routing-controls", "Adjust the layout", "Use these controls to change graph spacing and edge geometry."),
            QuickGuideStep("#panel-toggle", "Controls", "This toggle shows or hides Controls, which contains legends, relation controls, and detailed How to use help."),
        ))
```

Add a private serializer that returns `None` without reading the property when disabled, raises the specified error for an enabled `None`, and converts frozen steps with `dataclasses.asdict`. Pass its result through `_script_json` to the existing assembled runtime's bootstrap configuration, beside `docData` rather than adding a separate page-level script:

```javascript
const QUICK_GUIDE_CONFIG = @@OFFICINA_QUICK_GUIDE_CONFIG@@;
```

Extend `_build_html_with_elk(..., quick_guide: QuickGuide | None = None)` with the serialized placeholder. Keep `build_html_with_elk(...)` behavior unchanged by passing `quick_guide=None` directly; do not route it through `ElkHtmlRenderer` and create recursion. `ElkHtmlRenderer._render_graph(...)` passes its effective guide to `_build_html_with_elk(...)`.

- [ ] **Step 4: Run the contract tests and verify they pass**

Run: `pytest tests/test_visualization_renderer_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Optional authorized checkpoint commit**

```bash
git add src/officina/visualization/elk_html_renderer.py src/officina/visualization/html_renderer/runtime/bootstrap.js tests/test_visualization_renderer_cli.py
git commit -m "feat(visualization): add renderer quick-guide contract"
```

---

### Task 2: Side-effect-free Quick guide browser UI

**Files:**
- Modify: `src/officina/visualization/html_renderer/page.html:36-74,205-210`
- Modify: `src/officina/visualization/html_renderer/viewer.css:29-192,957-1010`
- Modify: `src/officina/visualization/html_renderer/assets.py:16-38`
- Create: `src/officina/visualization/html_renderer/runtime/quick_guide.js`
- Modify: `src/officina/visualization/html_renderer/runtime/graph_actions.js:232-244`
- Modify: `src/officina/visualization/html_renderer/runtime/filtering.js:484-490`
- Modify: `src/officina/visualization/html_renderer/runtime/controls.js:325-338`
- Create: `tests/test_visualization_quick_guide_browser.py`

**Interfaces:**
- Consumes: `QUICK_GUIDE_CONFIG` and the existing `viewerStateKey`.
- Produces: `startQuickGuide({manual?: boolean} = {}) -> void` and `startQuickGuideIfNeeded() -> void` inside the assembled browser closure.
- Produces: toolbar button `#quick-guide-btn`, dialog `#quick-guide-dialog`, highlight `#quick-guide-highlight`, and Back/Next/Finish/Skip controls.
- Persists: only the string `"dismissed"` at `${viewerStateKey}::quick-guide` after Finish, Skip, or Escape, using the same best-effort `localStorage` pattern as existing viewer state.

- [ ] **Step 1: Write the failing focused browser test**

Generate a minimal enabled viewer with two guide steps and use the existing `require_chrome()` / `run_html()` harness. After the initial layout settles, assert `renderVersion === 1`, capture the complete persisted/runtime state, and exercise the guide:

```javascript
await waitFor(() => !document.getElementById("quick-guide-dialog").hidden);
if (renderVersion !== 1) throw new Error(`expected one startup render, got ${renderVersion}`);
const isolationSnapshot = () => JSON.stringify({
  graph: graphStateSnapshot(),
  hiddenTypes: Array.from(hiddenTypes).sort(),
  hiddenEdgeTypes: Array.from(hiddenEdgeTypes).sort(),
  manualPositions: Array.from(manualPositions.entries()),
  routingConfig: {...routingConfig},
  filterState: serializeFilterState(),
  presentationNodes: serializePresentationNodesState(),
  panX, panY, zoomLevel, leftPanelCollapsed, rightPanelCollapsed,
  leftPanelWidth, rightPanelWidth,
  mainStorage: localStorage.getItem(viewerStateKey),
  sidebarStorage: localStorage.getItem(viewerStateKey + "::sidebar"),
  undo: graphUndoStack.map(action => [graphStateKey(action.before), graphStateKey(action.after)]),
  redo: graphRedoStack.map(action => [graphStateKey(action.before), graphStateKey(action.after)]),
  renderVersion,
});
closeQuickGuide();
runGraphAction(() => dimmedNodes.add("result"), {renderMode: "presentation"});
runGraphAction(() => dimmedNodes.delete("result"), {renderMode: "presentation"});
startQuickGuide({manual: true});
const before = isolationSnapshot();
if (document.getElementById("quick-guide-step").textContent !== "1 of 2") throw new Error("wrong initial guide step");
document.getElementById("quick-guide-next").click();
if (document.getElementById("quick-guide-step").textContent !== "2 of 2") throw new Error("Next did not advance");
document.getElementById("quick-guide-back").click();
if (document.getElementById("quick-guide-step").textContent !== "1 of 2") throw new Error("Back did not return");
document.getElementById("quick-guide-skip").click();
if (!document.getElementById("quick-guide-dialog").hidden) throw new Error("Skip did not close the guide");
if (localStorage.getItem(viewerStateKey + "::quick-guide") !== "dismissed") throw new Error("dismissal was not persisted per graph");
localStorage.removeItem(viewerStateKey + "::quick-guide");
document.getElementById("quick-guide-btn").click();
if (document.getElementById("quick-guide-dialog").hidden) throw new Error("manual reopen failed");
window.dispatchEvent(new Event("resize"));
window.dispatchEvent(new Event("scroll"));
if (before !== isolationSnapshot()) throw new Error("guide changed viewer state or triggered a render");
document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
if (!document.getElementById("quick-guide-dialog").hidden) throw new Error("Escape did not close the guide");
if (localStorage.getItem(viewerStateKey + "::quick-guide") !== "dismissed") throw new Error("Escape did not persist dismissal");
if (before !== isolationSnapshot()) throw new Error("guide changed viewer state or triggered a render");
```

While the guide owns focus, dispatch representative mutation shortcuts (`r`, `/`, and Ctrl/Cmd+Z) and assert they neither move focus away from the guide nor change the isolation snapshot. The two seeded reversible actions make Ctrl/Cmd+Z capable of mutating state if the capture-phase guard is absent; assert both history stacks are unchanged. Then focus the existing outside-guide `#fit-btn`, dispatch `f`, and assert the ordinary viewer shortcut still works while the non-modal guide remains open. Also assert that the callout is `role="dialog"` with nonempty `aria-labelledby` / `aria-describedby`; Finish closes and persists dismissal; Skip and Escape close; and focus returns to the button that manually reopened the guide. Add a missing-target step before a valid step and assert startup skips it rather than throwing or blocking the graph. Assert the disabled renderer keeps `#quick-guide-btn` hidden and never opens the dialog. Run the same enabled HTML through `run_html(..., window_size="700,700")` and assert the callout rectangle stays within the viewport, its focused action remains visible, and the callout becomes internally scrollable when needed.

Finally, temporarily replace `Storage.prototype.getItem` and `setItem` with functions that throw, open and dismiss the guide inside `try`, restore both prototypes in `finally`, and assert no exception escaped, the dialog closed, the graph remained usable, and `renderVersion` did not change.

- [ ] **Step 2: Run the browser test and verify it fails**

Run: `pytest tests/test_visualization_quick_guide_browser.py -q`

Expected: FAIL because the guide DOM and runtime do not exist.

- [ ] **Step 3: Add reusable guide markup and styling**

Place a hidden `Quick guide` button beside the existing Fit control, reusing `toolbar-btn`. Add one hidden dialog and one highlight element near the existing tooltip; do not clone target controls or move them in the DOM. Style a fixed callout and outline with the viewer's existing colors and focus treatment. Clamp the callout on every side of the viewport, give it a bounded height with internal scrolling, and apply the existing `max-width: 720px` responsive breakpoint. Disable decorative highlight pointer events and respect `prefers-reduced-motion` by using no required animation.

- [ ] **Step 4: Implement the isolated guide controller**

Add two Quick-guide-local best-effort persistence functions, following the existing viewer-state `try`/`catch` pattern. They derive one auxiliary key from `viewerStateKey` but never write to the main viewer-state payload:

```javascript
function readQuickGuideDismissal() {
  try { return window.localStorage.getItem(viewerStateKey + "::quick-guide"); }
  catch (error) { return null; }
}

function writeQuickGuideDismissal() {
  try { window.localStorage.setItem(viewerStateKey + "::quick-guide", "dismissed"); }
  catch (error) {}
}
```

Add `runtime/quick_guide.js` after `runtime/layout.js` and before `runtime/controls.js` in `_RUNTIME_ASSETS`. Implement these rules directly:

```javascript
let quickGuideIndex = -1;
let quickGuideReturnFocus = null;

function availableQuickGuideSteps() {
  return (QUICK_GUIDE_CONFIG?.steps || []).filter(step => {
    try { return document.querySelector(step.target); }
    catch (error) { return false; }
  });
}

function closeQuickGuide({dismiss = false} = {}) {
  if (dismiss) writeQuickGuideDismissal();
  // Hide dialog/highlight, remove guide-only classes, and restore prior focus.
  // Do not call saveViewerState(), updateVisibilityFull(), fitGraph(), or panel toggles.
}

function startQuickGuide({manual = false} = {}) {
  const steps = availableQuickGuideSteps();
  if (!QUICK_GUIDE_CONFIG || !steps.length) return;
  if (!manual && readQuickGuideDismissal() === "dismissed") return;
  quickGuideReturnFocus = document.activeElement;
  quickGuideIndex = 0;
  renderQuickGuideStep(steps);
}
```

`renderQuickGuideStep` uses `getBoundingClientRect()` to position the existing highlight and callout, updates `aria-labelledby`, `aria-describedby`, `aria-live`, button disabled/hidden states, and focuses Next or Finish. Back and Next never write storage. Finish, Skip, and Escape close with `dismiss: true`; manual reopening ignores stored dismissal. A capturing Escape handler must stop the existing deselect shortcut only while the dialog is open. Window resize/scroll may reposition an open callout but must not mutate graph state or force layout.

- [ ] **Step 5: Guard existing global shortcuts while the guide owns focus**

Expose side-effect-free `isQuickGuideOpen() -> boolean` and `quickGuideOwnsFocus() -> boolean` functions from `quick_guide.js`. At the start of each existing document-level keydown listener in `graph_actions.js`, `filtering.js`, and `controls.js`, return only when the guide contains the active element:

```javascript
function quickGuideOwnsFocus() {
  return isQuickGuideOpen() && quickGuideDialog.contains(document.activeElement);
}

document.addEventListener("keydown", event => {
  if (quickGuideOwnsFocus()) return;
  // existing handler body remains unchanged
});
```

This is necessary in `graph_actions.js` even though the guide captures Escape: its capture-phase undo/redo listener is registered before `quick_guide.js`, so propagation alone cannot protect graph state. Keep a separate capturing Escape handler based on `isQuickGuideOpen()` so Escape cancels the guide even if the user clicked the canvas. Do not disable toolbar or canvas pointer interactions; the guide is non-modal, and only accidental global shortcuts while guide controls own focus are suppressed.

- [ ] **Step 6: Start only after the existing first render settles**

Change the existing load callback without adding a second render path:

```javascript
window.addEventListener("load", async () => {
  await updateVisibilityFull();
  startQuickGuideIfNeeded();
});
```

The guide button is unhidden only when `QUICK_GUIDE_CONFIG` contains at least one target present in the current document.

- [ ] **Step 7: Run focused browser and existing viewer tests**

Run: `pytest tests/test_visualization_quick_guide_browser.py tests/test_visualization_browser.py -q`

Expected: PASS, with existing viewer interactions unchanged.

- [ ] **Step 8: Optional authorized checkpoint commit**

```bash
git add src/officina/visualization/html_renderer/page.html src/officina/visualization/html_renderer/viewer.css src/officina/visualization/html_renderer/assets.py src/officina/visualization/html_renderer/runtime/quick_guide.js src/officina/visualization/html_renderer/runtime/graph_actions.js src/officina/visualization/html_renderer/runtime/filtering.js src/officina/visualization/html_renderer/runtime/controls.js tests/test_visualization_quick_guide_browser.py
git commit -m "feat(visualization): add optional quick-guide UI"
```

---

### Task 3: Enable and tailor the guide for math-dependency graphs

**Files:**
- Modify: `src/officina/visualization/base_renderer_cli.py:12-14,61-116`
- Modify: `skills/math-dependency-graph/_rtx/_graph_builder.py:14-15,236-244`
- Modify: `skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py:72-109`
- Modify: `src/officina/visualization/html_renderer/README.md:9-20,67-112`

**Interfaces:**
- Produces: `base_renderer_cli.main(argv: list[str] | None = None, *, renderer: ElkHtmlRenderer | None = None) -> int`, using the selected instance for validation, reduction, and rendering.
- Produces: `MathDependencyGraphRenderer(ElkHtmlRenderer)` overriding `quick_guide` while reusing the generic steps.
- Consumes: `render_html(render_argv, renderer=MathDependencyGraphRenderer(quick_guide_enabled=True))` at the existing deterministic math render boundary.

- [ ] **Step 1: Write the failing math-renderer test**

Extend the existing end-to-end renderer test to assert that math output opts in and that the subclass's first step teaches the dependency direction without replacing the generic guide:

```python
self.assertIn('const QUICK_GUIDE_CONFIG = {', html)
self.assertIn('"title": "Read mathematical dependencies"', html)
self.assertIn("Follow arrows from prerequisites toward the results they support.", html)
self.assertIn('"title": "Act on the graph"', html)
```

Add a CLI unit test using a spy renderer and `--reduce-transitive-edges` to prove `main(..., renderer=spy)` calls that same instance's `validate`, `reduce_graph_json_transitive_edges`, and `render_graph_html`, and does not silently fall back to `_DEFAULT_RENDERER` or `build_html_with_elk`:

```python
class SpyRenderer(ElkHtmlRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def validate(self, graph_json) -> None:
        self.calls.append("validate")
        super().validate(graph_json)

    def reduce_graph_json_transitive_edges(self, graph_json):
        self.calls.append("reduce")
        return super().reduce_graph_json_transitive_edges(graph_json)

    def render_graph_html(self, graph_json, **kwargs) -> str:
        self.calls.append("render")
        return super().render_graph_html(graph_json, **kwargs)


assert main([str(source), "--html-out", str(target), "--reduce-transitive-edges"], renderer=spy) == 0
assert spy.calls == ["validate", "reduce", "render"]
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `pytest tests/test_visualization_renderer_cli.py skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py -q`

Expected: FAIL because CLI renderer injection and the math subclass do not exist.

- [ ] **Step 3: Route the existing CLI through an injected renderer instance**

Add the keyword-only renderer parameter and select `renderer or _DEFAULT_RENDERER` before validation. Use that same instance throughout the existing main flow:

```python
selected_renderer = renderer or _DEFAULT_RENDERER
selected_renderer.validate(doc)
if args.reduce_transitive_edges:
    doc, removed_edges = selected_renderer.reduce_graph_json_transitive_edges(doc)
html_path.write_text(
    selected_renderer.render_graph_html(doc, reduction_note=reduction_note),
    encoding="utf-8",
)
```

Inside `main`, do not call the module-level `validate_document` or `reduce_transitive_edges` helpers because those intentionally retain their existing default-renderer behavior for external callers. Do not add a public CLI flag: the instance flag is the intended configuration boundary, and existing command-line callers must remain unchanged.

- [ ] **Step 4: Add the smallest math-specific subclass and opt in**

Keep it in `_graph_builder.py`, beside the existing math-only presentation merge, rather than creating a new subsystem:

```python
class MathDependencyGraphRenderer(ElkHtmlRenderer):
    @property
    def quick_guide(self) -> QuickGuide:
        guide = super().quick_guide
        assert guide is not None
        math_step = QuickGuideStep(
            target="#canvas-wrap",
            title="Read mathematical dependencies",
            body="Follow arrows from prerequisites toward the results they support. Click any node or edge for the document evidence.",
        )
        return replace(guide, steps=(math_step, *guide.steps[1:]))


render_html(
    render_argv,
    renderer=MathDependencyGraphRenderer(quick_guide_enabled=True),
)
```

This replaces the generic canvas step instead of adding a duplicate. All remaining generic steps and the existing “How to use” section are reused.

- [ ] **Step 5: Document the renderer API and extension rule**

Add one short public example to the renderer README:

```python
renderer = ElkHtmlRenderer(quick_guide_enabled=True)

class DomainRenderer(ElkHtmlRenderer):
    @property
    def quick_guide(self) -> QuickGuide:
        guide = super().quick_guide
        return replace(guide, steps=(domain_step, *guide.steps[1:]))
```

State that the guide is renderer configuration, not canonical graph data; `False` preserves the old viewer; and subclasses should reuse generic steps instead of copying the viewer help.

- [ ] **Step 6: Run focused math and renderer tests**

Run: `pytest tests/test_visualization_renderer_cli.py tests/test_visualization_quick_guide_browser.py skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py -q`

Expected: PASS.

- [ ] **Step 7: Run the affected visualization suite**

Run: `pytest tests/test_visualization_browser.py tests/test_visualization_containment_edges_browser.py tests/test_visualization_filtering.py tests/test_visualization_graph.py tests/test_visualization_inspector_and_bezier_browser.py tests/test_visualization_node_readability_browser.py tests/test_visualization_projection_arrangements_browser.py tests/test_visualization_projection_browser.py tests/test_visualization_projection_policy.py tests/test_visualization_renderer_cli.py tests/test_visualization_quick_guide_browser.py -q`

Expected: PASS. If Chrome is unavailable, report browser tests as unverified rather than calling the plan fully verified.

- [ ] **Step 8: Inspect scope and generated behavior**

Run: `git diff --check`

Run: `git diff --name-only`

Expected owned implementation paths only in the isolated worktree: the renderer, its assets/runtime/tests/README, the math builder, and its focused test. Confirm the canonical graph schema is unchanged and no generated HTML artifact is committed. If commit authorization was given and files were staged, also run `git diff --cached --check` and `git diff --cached --name-only`; do not stage or commit from the original dirty checkout.

- [ ] **Step 9: Optional authorized checkpoint commit**

```bash
git add src/officina/visualization/base_renderer_cli.py src/officina/visualization/html_renderer/README.md skills/math-dependency-graph/_rtx/_graph_builder.py skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py
git commit -m "feat(math-graph): enable tailored quick guide"
```
