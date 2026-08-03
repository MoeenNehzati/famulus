"""Disposable browser matrix for graph node-removal projection behavior."""

from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from officina.common.visualization.elk_html_renderer import build_html_with_elk


CHROME = shutil.which("google-chrome")


def entity(identifier, position, edges=(), *, container=None, node_type="source"):
    result = {
        "id": identifier,
        "type": node_type,
        "category": "node",
        "short_title": identifier,
        "position": position,
        "connects_to": [dict(edge) for edge in edges],
    }
    if container:
        result["container"] = container
    return result


def edge(target, edge_type="direct", **extra):
    return {"to": target, "type": edge_type, **extra}


def payload(entities):
    return {
        "schema_version": 2,
        "graph_id": "temporary-projection-arrangement",
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [
            {"id": "direct", "label": "Direct"},
            {"id": "indirect", "label": "Indirect"},
            {"id": "uses-interface", "label": "Uses interface"},
            {"id": "indirectly-uses-interface", "label": "Indirectly uses interface"},
            {"id": "binds-interface", "label": "Binds interface"},
            {"id": "indirectly-binds-interface", "label": "Indirectly binds interface"},
            {"id": "indirectly-depends-on", "label": "Indirectly depends"},
        ],
        "relation_semantics": {
            "transformations": {"node_omission": {"rules": [
                {
                    "id": "to-direct",
                    "causes": ["user-hidden"],
                    "left_types": ["direct", "indirect"],
                    "right_types": ["direct"],
                    "outcomes": [{"type": "indirect", "fidelity": "exact"}],
                },
                {
                    "id": "to-used-interface",
                    "causes": ["user-hidden"],
                    "left_types": ["uses-interface", "indirectly-uses-interface", "direct", "indirect"],
                    "right_types": ["uses-interface"],
                    "outcomes": [{"type": "indirectly-uses-interface", "fidelity": "exact"}],
                },
                {
                    "id": "through-binding",
                    "causes": ["user-hidden"],
                    "left_types": ["uses-interface", "indirectly-uses-interface"],
                    "right_types": ["binds-interface"],
                    "outcomes": [
                        {"type": "indirectly-uses-interface", "fidelity": "exact"},
                        {"type": "indirectly-depends-on", "fidelity": "degraded"},
                    ],
                },
                {
                    "id": "binding-layer",
                    "causes": ["user-hidden"],
                    "left_types": ["binds-interface", "indirectly-binds-interface"],
                    "right_types": ["binds-interface"],
                    "outcomes": [{"type": "indirectly-binds-interface", "fidelity": "exact"}],
                },

                {"id": "dependency-through-binding", "causes": ["user-hidden"], "left_types": ["direct", "indirect"], "right_types": ["binds-interface"], "outcomes": [{"type": "indirectly-depends-on", "fidelity": "degraded"}]},
                {"id": "continue-coarse", "causes": ["user-hidden"], "left_types": ["indirectly-depends-on"], "right_types": ["direct", "uses-interface", "binds-interface"], "outcomes": [{"type": "indirectly-depends-on", "fidelity": "degraded"}]},
            ]}},
            "subsumptions": [
                {"stronger_type": "direct", "weaker_types": ["indirect"]},
                {"stronger_type": "indirect", "weaker_types": ["indirectly-depends-on"]},
                {"stronger_type": "uses-interface", "weaker_types": ["indirectly-uses-interface"]},
                {"stronger_type": "indirectly-uses-interface", "weaker_types": ["indirectly-depends-on"]},
                {"stronger_type": "binds-interface", "weaker_types": ["indirectly-binds-interface"]},
            ],
        },
        "ui": {"edge_styles": {
            "direct": {"color": "#b45309"},
            "indirect": {"color": "#64748b", "dash": "3 5"},
        }},
        "entities": entities,
    }


def run_case(name, entities, assertions):
    if CHROME is None:
        # famulus-skip: category=capability-unavailable; reason=Google Chrome is not installed; alternate=projection policy tests cover transformation semantics without a browser
        pytest.skip("google-chrome unavailable")
    helpers = r'''
      const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
      const node = id => document.querySelector(`[data-node-id="${id}"]`);
      const paths = (source, target, type = null) => Array.from(document.querySelectorAll(
        `.edge-path[data-source-node-id="${source}"][data-target-node-id="${target}"]`
      )).filter(path => path.style.display !== "none" && (!type || path.dataset.edgeType === type));
      const one = (source, target, type = null) => paths(source, target, type)[0] || null;
      const check = (condition, message) => { if (!condition) throw new Error(message); };
      const hide = async id => {
        check(node(id), `missing node ${id}`);
        node(id).dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
        await delay(70);
      };
      const restore = async id => {
        const label = entityMap.get(id).short_title;
        const item = Array.from(document.querySelectorAll(".hidden-node-item"))
          .find(candidate => candidate.textContent.includes(label));
        check(item, `missing restore control ${id}`);
        item.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
        await delay(70);
      };
      const undo = async () => {
        document.dispatchEvent(new KeyboardEvent("keydown", {key: "z", ctrlKey: true, bubbles: true}));
        await delay(70);
      };
      const redo = async () => {
        document.dispatchEvent(new KeyboardEvent("keydown", {key: "y", ctrlKey: true, bubbles: true}));
        await delay(70);
      };
      const toggleRelation = async type => {
        const row = document.querySelector(`.legend-row[data-legend-kind="edge"][data-type="${type}"]`);
        check(row, `missing relation legend ${type}`);
        row.click();
        await delay(70);
      };
    '''
    script = f'''<script>
    window.addEventListener("load", () => setTimeout(async () => {{
      try {{
        {helpers}
        {assertions}
        document.body.dataset.testStatus = "PASS";
      }} catch (error) {{
        document.body.dataset.testStatus = "FAIL:" + (error.message || String(error));
      }}
    }}, 100));
    </script></body>'''
    html = build_html_with_elk(payload(entities)).replace("</body>", script)
    path = Path(f"/tmp/officina-projection-{name}.html")
    path.write_text(html, encoding="utf-8")
    with tempfile.TemporaryDirectory() as profile:
        result = subprocess.run([
            CHROME, "--headless", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--disable-crash-reporter",
            f"--user-data-dir={profile}", "--virtual-time-budget=3500",
            "--dump-dom", path.as_uri(),
        ], check=True, capture_output=True, text=True)
    marker = 'data-test-status="'
    start = result.stdout.find(marker)
    status = result.stdout[start + len(marker):result.stdout.find('"', start + len(marker))] if start >= 0 else "FAIL:status missing"
    assert status == "PASS", status


def test_linear_dependency_hide_restore():
    run_case("linear", [
        entity("X", 0, [edge("Y")]), entity("Y", 1, [edge("Z")]), entity("Z", 2),
    ], '''
      await hide("Y");
      const derived = one("X", "Z", "indirect");
      check(derived, "linear projection missing");
      check(derived.__edgeMeta.metadata.projection.witness_path.join("|") === "X|Y|Z", "linear provenance wrong");
      await restore("Y");
      check(!one("X", "Z", "indirect") && one("X", "Y", "direct") && one("Y", "Z", "direct"), "linear restore wrong");
    ''')


def test_two_hidden_intermediates_and_partial_restore():
    run_case("multi-hop", [
        entity("A", 0, [edge("B")]), entity("B", 1, [edge("C")]),
        entity("C", 2, [edge("D")]), entity("D", 3),
    ], '''
      await hide("B"); await hide("C");
      const derived = one("A", "D", "indirect");
      check(derived, "multi-hop projection missing");
      check(derived.__edgeMeta.metadata.projection.omitted_nodes.join("|") === "B|C", "multi-hop omissions wrong");
      await restore("B");
      check(!one("A", "D", "indirect") && one("B", "D", "indirect"), "partial restore boundary wrong");
    ''')


def test_branching_hidden_node_projects_each_branch():
    run_case("branch", [
        entity("X", 0, [edge("Y")]), entity("Y", 1, [edge("Z"), edge("W")]),
        entity("Z", 2), entity("W", 3),
    ], '''
      await hide("Y");
      check(one("X", "Z", "indirect") && one("X", "W", "indirect"), "branch projection incomplete");
    ''')


def test_converging_hidden_paths_are_deduplicated():
    run_case("converge", [
        entity("X", 0, [edge("A"), edge("B")]), entity("A", 1, [edge("Z")]),
        entity("B", 2, [edge("Z")]), entity("Z", 3),
    ], '''
      await hide("A"); await hide("B");
      const projected = paths("X", "Z", "indirect");
      check(projected.length === 1, `converging paths rendered ${projected.length} edges`);
      check(projected[0].__edgeMeta.metadata.represented_count === 4, "converging provenance did not merge canonical edges");
    ''')


def test_direct_edge_precedence_suppresses_indirect_duplicate():
    run_case("precedence", [
        entity("X", 0, [edge("Y"), edge("Z")]), entity("Y", 1, [edge("Z")]), entity("Z", 2),
    ], '''
      await hide("Y");
      check(paths("X", "Z").length === 1, "direct endpoint duplicated");
      check(one("X", "Z").dataset.edgeType === "direct" && one("X", "Z").dataset.bundle !== "true", "direct edge lost precedence");
    ''')


def test_interface_use_composes_through_hidden_binding():
    run_case("use-binding", [
        entity("consumer", 0, [edge("export", "uses-interface")]),
        entity("export", 1, [edge("child", "binds-interface")], node_type="interface"),
        entity("child", 2, node_type="interface"),
    ], '''
      await hide("export");
      check(one("consumer", "child", "indirectly-uses-interface"), "interface-use binding projection missing");
    ''')


def test_binding_chain_composes_through_hidden_export():
    run_case("bind-binding", [
        entity("root", 0, [edge("middle", "binds-interface")], node_type="interface"),
        entity("middle", 1, [edge("leaf", "binds-interface")], node_type="interface"),
        entity("leaf", 2, node_type="interface"),
    ], '''
      await hide("middle");
      check(one("root", "leaf", "indirectly-binds-interface"), "binding-layer projection missing");
    ''')


def test_hidden_module_uses_declared_implementation_target():
    run_case("implementation", [
        entity("consumer", 0, [edge("module.interface", "uses-interface", projection_target="module.impl")]),
        entity("module", 1, node_type="module"),
        entity("module.interface", 2, container="module", node_type="interface"),
        entity("module.impl", 3, [edge("storage", "uses-interface")], container="module"),
        entity("storage", 4, node_type="interface"),
    ], '''
      await hide("module");
      const derived = one("consumer", "storage", "indirectly-uses-interface");
      check(derived, "implementation-target projection missing");
      check(derived.__edgeMeta.metadata.projection.omitted_nodes.includes("module.interface"), "implementation provenance lost interface");
      check(derived.__edgeMeta.metadata.projection.omitted_nodes.includes("module.impl"), "implementation provenance lost source");
    ''')


def test_hidden_module_does_not_cross_to_sibling_gateway():
    run_case("sibling-isolation", [
        entity("consumer", 0, [edge("module.interface", "uses-interface", projection_target="module.impl")]),
        entity("module", 1, node_type="module"),
        entity("module.interface", 2, container="module", node_type="interface"),
        entity("module.impl", 3, container="module"),
        entity("module.gateway", 4, [edge("setup", "uses-interface")], container="module"),
        entity("setup", 5, node_type="interface"),
    ], '''
      await hide("module");
      check(!one("consumer", "setup"), "projection crossed unrelated sibling gateway");
    ''')


def test_relation_removal_removes_and_restores_projection():
    run_case("relation-toggle", [
        entity("X", 0, [edge("Y")]), entity("Y", 1, [edge("Z")]), entity("Z", 2),
    ], '''
      await hide("Y");
      check(one("X", "Z", "indirect"), "initial relation projection missing");
      await toggleRelation("direct");
      check(!one("X", "Z", "indirect"), "projection survived relation removal");
      await toggleRelation("direct");
      check(one("X", "Z", "indirect"), "projection did not return with relation");
    ''')


def test_cycle_does_not_create_self_edge():
    run_case("cycle", [
        entity("X", 0, [edge("Y")]), entity("Y", 1, [edge("X")]),
    ], '''
      await hide("Y");
      check(!one("X", "X"), "cycle produced forbidden self edge");
    ''')


def test_undo_redo_and_hidden_target_behavior():
    run_case("history-target", [
        entity("X", 0, [edge("Y")]), entity("Y", 1, [edge("Z")]), entity("Z", 2),
    ], '''
      await hide("Y");
      check(one("X", "Z", "indirect"), "projection missing before undo");
      await undo();
      check(!one("X", "Z", "indirect") && one("X", "Y", "direct"), "undo failed");
      await redo();
      check(one("X", "Z", "indirect"), "redo failed");
      await restore("Y");
      await hide("Z");
      check(!one("Y", "Z") && !one("X", "Z"), "hidden target retained incoming edge");
      await restore("Z");
      check(one("Y", "Z", "direct"), "target restore failed");
    ''')


def test_mixed_dependency_use_and_binding_chain():
    run_case("mixed-chain", [
        entity("S", 0, [edge("H1")]),
        entity("H1", 1, [edge("H2", "uses-interface")]),
        entity("H2", 2, [edge("T", "binds-interface")], node_type="interface"),
        entity("T", 3, node_type="interface"),
    ], '''
      await hide("H1"); await hide("H2");
      const derived = one("S", "T", "indirectly-uses-interface");
      check(derived, "mixed semantic projection missing");
      check(derived.__edgeMeta.metadata.projection.witness_path.join("|") === "S|H1|H2|T", "mixed semantic witness wrong");
      check(!one("S", "T", "indirect") && !one("S", "T", "indirectly-binds-interface"), "mixed chain produced wrong semantic type");
    ''')


def test_multiple_sources_remain_independent_at_convergence():
    run_case("multi-source", [
        entity("S1", 0, [edge("H", "uses-interface")]),
        entity("S2", 1, [edge("H", "uses-interface")]),
        entity("H", 2, [edge("T", "binds-interface")], node_type="interface"),
        entity("T", 3, node_type="interface"),
    ], '''
      await hide("H");
      check(one("S1", "T", "indirectly-uses-interface"), "first source projection missing");
      check(one("S2", "T", "indirectly-uses-interface"), "second source projection missing");
      check(!one("S1", "S2") && !one("S2", "S1"), "convergence created cross-source edge");
    ''')


def test_hidden_cycle_terminates_and_preserves_visible_exit():
    run_case("cycle-exit", [
        entity("S", 0, [edge("H1", "uses-interface")]),
        entity("H1", 1, [edge("H2", "binds-interface")], node_type="interface"),
        entity("H2", 2, [edge("H1", "binds-interface"), edge("T", "binds-interface")], node_type="interface"),
        entity("T", 3, node_type="interface"),
    ], '''
      await hide("H1"); await hide("H2");
      const derived = one("S", "T", "indirectly-uses-interface");
      check(derived, "cycle exit projection missing");
      check(paths("S", "T", "indirectly-uses-interface").length === 1, "cycle duplicated exit projection");
      check(!one("S", "S") && !one("S", "H1") && !one("S", "H2"), "cycle produced forbidden edge");
      const ids = derived.__edgeMeta.metadata.represented_edges.map(item => item.edge_id);
      check(ids.length === 3, "cycle-only edge contaminated successful witness");
    ''')


def test_same_endpoints_keep_distinct_derived_semantics_in_bundle():
    run_case("mixed-bundle", [
        entity("S", 0, [edge("H", "uses-interface"), edge("H", "binds-interface")]),
        entity("H", 1, [edge("T", "binds-interface")], node_type="interface"),
        entity("T", 2, node_type="interface"),
    ], '''
      await hide("H");
      const bundled = one("S", "T");
      check(bundled && bundled.dataset.edgeType === "relationship-bundle", "mixed semantic edges were not bundled");
      check(bundled.__edgeMeta.bundle === true, "mixed endpoint relation lacks bundle metadata");
      const types = new Set(bundled.__edgeMeta.bundle_types);
      check(types.has("indirectly-uses-interface") && types.has("indirectly-binds-interface") && types.size === 2, "bundle lost a semantic constituent");
    ''')


def test_parallel_canonical_witnesses_merge_without_duplicate_path():
    run_case("parallel-witness", [
        entity("S", 0, [edge("H", "uses-interface"), edge("H", "uses-interface")]),
        entity("H", 1, [edge("T", "binds-interface")], node_type="interface"),
        entity("T", 2, node_type="interface"),
    ], '''
      await hide("H");
      const projected = paths("S", "T", "indirectly-uses-interface");
      check(projected.length === 1, `parallel witnesses rendered ${projected.length} paths`);
      check(projected[0].__edgeMeta.metadata.represented_count === 3, "parallel canonical witness provenance was dropped or duplicated");
    ''')


def test_collapse_aggregates_without_marking_dependency_indirect():
    run_case("collapse", [
        entity("A", 0, node_type="module"),
        entity("A.s", 1, [edge("B.s")], container="A"),
        entity("B", 2, node_type="module"),
        entity("B.s", 3, container="B"),
    ], '''
      node("A").dispatchEvent(new MouseEvent("dblclick", {bubbles: true, altKey: true}));
      await delay(250);
      node("B").dispatchEvent(new MouseEvent("dblclick", {bubbles: true, altKey: true}));
      await delay(250);
      const aggregate = one("A", "B", "direct");
      check(aggregate && aggregate.dataset.aggregate === "true", "collapsed containers lack aggregate edge");
      const directLegend = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="direct"] .legend-icon path');
      check(getComputedStyle(aggregate).strokeDasharray === getComputedStyle(directLegend).strokeDasharray, "aggregation overrode the relationship type dash pattern");
      const aggregatePresentation = document.querySelector('.legend-row[data-legend-kind="edge-presentation"][data-type="aggregate"]');
      check(aggregatePresentation, "aggregate metadata presentation missing from legend");
      check(getComputedStyle(aggregatePresentation.querySelector(".legend-icon path:not(.edge-presentation-outline)")).strokeWidth === getComputedStyle(aggregate).strokeWidth, "aggregate legend width diverged from rendered edge");
      check(aggregate.style.filter.includes("edge-presentation-filter"), "hidden-detail summary edge lacks halo");
      check(aggregatePresentation.querySelector(".edge-presentation-outline"), "hidden-detail summary legend lacks halo");
      check(!one("A", "B", "indirect"), "collapse incorrectly created indirect dependency");
    ''')


def test_visible_projection_target_does_not_replace_hidden_interface():
    run_case("visible-target", [
        entity("consumer", 0, [edge("export", "uses-interface", projection_target="impl")]),
        entity("export", 1, [edge("child", "binds-interface")], node_type="interface"),
        entity("impl", 2),
        entity("child", 3, node_type="interface"),
    ], '''
      await hide("export");
      check(one("consumer", "child", "indirectly-uses-interface"), "hidden export did not follow authored binding");
      check(!one("consumer", "impl"), "visible projection target replaced canonical interface path");
    ''')


def test_bulk_selection_hide_uses_same_projection_action():
    run_case("bulk-hide", [
        entity("S", 0, [edge("A")]), entity("A", 1, [edge("B")]),
        entity("B", 2, [edge("T")]), entity("T", 3),
    ], '''
      node("A").dispatchEvent(new MouseEvent("click", {bubbles: true}));
      await delay(220);
      node("B").dispatchEvent(new MouseEvent("click", {bubbles: true, ctrlKey: true}));
      await delay(220);
      document.getElementById("hide-selected-btn").click();
      await delay(100);
      check(hiddenNodes.has("A") && hiddenNodes.has("B") && hiddenNodes.size === 2, "bulk hide did not hide exact selection");
      check(selectedNodeIds.size === 0 && selectedNodeId === null, "bulk hide retained consumed selection");
      check(one("S", "T", "indirect"), "bulk hide did not create multi-hop projection");
    ''')


def test_overlapping_child_and_container_hides_restore_independently():
    run_case("overlap-container", [
        entity("S", 0, [edge("A")]),
        entity("K", 1, node_type="module"),
        entity("A", 2, [edge("B")], container="K"),
        entity("B", 3, [edge("T")], container="K"),
        entity("T", 4),
    ], '''
      await hide("A"); await hide("K");
      check(hiddenNodes.has("A") && hiddenNodes.has("K") && hiddenNodes.size === 2, "overlapping hides lost explicit state");
      await restore("K");
      check(hiddenNodes.has("A") && !hiddenNodes.has("K"), "restoring container erased child hide");
      check(node("B").style.display !== "none" && node("A").style.display === "none", "container restore visibility wrong");
      check(one("S", "B", "indirect"), "remaining child hide lacks projection");
      await restore("A");
      check(hiddenNodes.size === 0 && !one("S", "B", "indirect"), "final child restore left stale projection");
      await undo();
      check(hiddenNodes.has("A") && !hiddenNodes.has("K"), "undo did not restore child-only hide");
      await undo();
      check(hiddenNodes.has("A") && hiddenNodes.has("K"), "second undo did not restore overlapping hides");
      await redo(); await redo();
      check(hiddenNodes.size === 0, "redo sequence did not restore visible state");
    ''')


def test_move_hide_restore_preserves_manual_geometry_and_cleans_projection():
    run_case("move-hide", [
        entity("S", 0, [edge("A")]), entity("A", 1, [edge("B")]), entity("B", 2),
    ], '''
      const before = {...getEffectivePos("A")};
      node("A").dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0, clientX: 200, clientY: 200}));
      document.dispatchEvent(new MouseEvent("mousemove", {bubbles: true, clientX: 235, clientY: 220}));
      document.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, clientX: 235, clientY: 220}));
      await delay(80);
      const moved = {...getEffectivePos("A")};
      check(moved.x !== before.x || moved.y !== before.y, "drag did not move node");
      const canonical = one("S", "A", "direct");
      const movedPath = canonical.getAttribute("d");
      await hide("A");
      check(one("S", "B", "indirect")?.getAttribute("d"), "moved-node projection lacks geometry");
      await restore("A");
      const restored = getEffectivePos("A");
      check(restored.x === moved.x && restored.y === moved.y, "manual node position was lost");
      check(canonical.style.display !== "none" && canonical.getAttribute("d") === movedPath, "canonical moved edge geometry was not restored");
      check(!one("S", "B", "indirect"), "restoration left derived edge");
      check(document.querySelectorAll('.edge-arrow[data-derived="true"]').length === 0, "restoration left derived arrowhead");
    ''')


def test_mixed_dependency_to_binding_degrades_without_inventing_interface_use():
    run_case("unsupported-mix", [
        entity("S", 0, [edge("H")]),
        entity("H", 1, [edge("I", "binds-interface")]),
        entity("I", 2, node_type="interface"),
    ], '''
      await hide("H");
      check(one("S", "I", "indirectly-depends-on"), "mixed composition lost its coarse dependency");
      check(!one("S", "I", "indirectly-uses-interface"), "mixed composition invented interface use");
    ''')


def test_fallback_branch_survives_when_refined_branch_dead_ends():
    run_case("fallback-completes", [
        entity("S", 0, [edge("H1", "uses-interface")]),
        entity("H1", 1, [edge("H2", "binds-interface")], node_type="interface"),
        entity("H2", 2, [edge("T")], node_type="interface"),
        entity("T", 3),
    ], '''
      await hide("H1"); await hide("H2");
      const coarse = one("S", "T", "indirectly-depends-on");
      check(coarse, "greedy refined composition discarded the completing fallback branch");
      const witness = coarse.__edgeMeta.metadata.projection.witnesses[0];
      check(witness.fidelity === "degraded", "fallback witness lost degraded fidelity");
      check(witness.transitions.some(step => step.fidelity === "degraded"), "degraded transition provenance missing");
    ''')
