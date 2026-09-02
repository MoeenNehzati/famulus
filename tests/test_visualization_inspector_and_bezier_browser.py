"""Browser coverage for inspector navigation and advanced edge geometry."""

import json
import os
from pathlib import Path

from officina.visualization.elk_html_renderer import build_html_with_elk
from test_support.browser import require_chrome, run_html


REPO_ROOT = Path(__file__).resolve().parents[1]
MATH_DEPENDENCY_GRAPH = (
    REPO_ROOT
    / "skills/math-dependency-graph/assets/inference-from-random-restarts/results/extraction-latest.json"
)


def _task5_math_dependency_graph() -> Path:
    override = os.environ.get("FAMULUS_MATH_DEPENDENCY_GRAPH")
    return Path(override) if override else MATH_DEPENDENCY_GRAPH


def _run_browser_case(
    name: str,
    payload: dict,
    script: str,
    *,
    virtual_time_budget: int = 4000,
    wait_for_load: bool = True,
) -> None:
    chrome = require_chrome()
    runner_body = f"""async () => {{
          try {{
            document.body.dataset.testStatus = "RUNNING";
            {script}
            document.body.dataset.testStatus = "PASS";
          }} catch (error) {{
            document.body.dataset.testStatus = "FAIL:" + (error.message || String(error));
          }}
        }}"""
    if wait_for_load:
        runner = f'window.addEventListener("load", () => setTimeout({runner_body}, 150));'
    else:
        runner = f"({runner_body})();"
    html = build_html_with_elk(payload).replace(
        "</body>",
        f"""<script>
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        {runner}
        </script></body>""",
    )
    result = run_html(
        chrome,
        html,
        virtual_time_budget=virtual_time_budget,
    )
    marker = 'data-test-status="'
    marker_start = result.stdout.find(marker)
    status = (
        result.stdout[marker_start + len(marker) :].split('"', 1)[0]
        if marker_start >= 0
        else "MISSING"
    )
    assert status == "PASS", status


def _payload(edge_type: str = "link") -> dict:
    return {
        "schema_version": 2,
        "graph_id": "inspector-and-bezier",
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [{"id": edge_type, "label": edge_type.replace("-", " ").title()}],
        "entities": [
            {
                "id": "alpha",
                "type": "source",
                "category": "node",
                "short_title": "Alpha",
                "description": "Alpha description",
                "position": 0,
                "connects_to": [{"to": "beta", "type": edge_type}],
            },
            {
                "id": "beta",
                "type": "source",
                "category": "node",
                "short_title": "Beta",
                "description": "Beta description",
                "position": 1,
                "connects_to": [],
            },
        ],
    }


def test_declared_edge_presentation_controls_stroke_and_legend() -> None:
    payload = {
        "schema_version": 2,
        "graph_id": "declared-edge-presentation",
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [{"id": "supports", "label": "Supports"}],
        "ui": {
            "edge_styles": {"supports": {"color": "#2563eb"}},
            "edge_presentation": {
                "facets": [
                    {
                        "id": "provenance",
                        "label": "Provenance",
                        "field": "implicit",
                        "variants": [
                            {
                                "id": "explicit",
                                "equals": False,
                                "label": "Explicit",
                                "description": "Asserted by the source.",
                                "style": {"line_pattern": "solid", "opacity": 0.65},
                            },
                            {
                                "id": "inferred",
                                "equals": True,
                                "label": "Inferred",
                                "description": "Inferred from the source.",
                                "style": {"line_pattern": "dashed", "opacity": 0.45},
                            },
                        ],
                    }
                ]
            },
        },
        "entities": [
            {"id": "a", "type": "node", "category": "node", "short_title": "A", "position": 0, "connects_to": [{"to": "b", "type": "supports", "implicit": False}, {"to": "b", "type": "supports", "implicit": True}]},
            {"id": "b", "type": "node", "category": "node", "short_title": "B", "position": 1, "connects_to": []},
        ],
    }
    _run_browser_case(
        "declared-edge-presentation",
        payload,
        """
        const paths = Array.from(edgeLayer.querySelectorAll(".edge-path"));
        const explicitPath = paths.find(path => path.__edgeMeta?.implicit === false);
        const inferredPath = paths.find(path => path.__edgeMeta?.implicit === true);
        if (!explicitPath || !inferredPath) throw new Error("provenance edges are missing");
        if (paths.length !== 2 || paths.some(path => path.__edgeMeta?.bundle)) {
          throw new Error("edges with different presentation signatures were bundled");
        }
        if (explicitPath.hasAttribute("stroke-dasharray")) throw new Error("explicit edge is not solid");
        if (inferredPath.getAttribute("stroke-dasharray") !== "9 5") throw new Error("inferred edge is not dashed");
        if (explicitPath.style.strokeOpacity !== "0.65" || inferredPath.style.strokeOpacity !== "0.45") {
          throw new Error("declared edge opacity was not applied");
        }
        if (arrowForPath(explicitPath)?.getAttribute("fill-opacity") !== "0.65"
            || arrowForPath(inferredPath)?.getAttribute("fill-opacity") !== "0.45") {
          throw new Error("declared arrow opacity was not applied");
        }
        const rows = Array.from(document.querySelectorAll('[data-legend-kind="edge-presentation-variant"][data-facet="provenance"]'));
        if (rows.length !== 2) throw new Error("provenance legend does not show its present variants");
        if (rows.map(row => row.dataset.type).sort().join(",") !== "explicit,inferred") {
          throw new Error("provenance legend variants are incorrect");
        }
        """,
    )


def test_mathjax_typesets_dynamic_tooltip_and_inspector_content() -> None:
    payload = _payload()
    payload["renderer_dependencies"] = [
        {
            "id": "mathjax",
            "version": "3",
            "configuration": {"macros": {"RR": "\\mathbb{R}"}},
        }
    ]
    payload["entities"][0]["description"] = "Maps $x^2$ into $\\RR$."

    _run_browser_case(
        "dynamic-mathjax-content",
        payload,
        """
        if (!window.MathJax?.typesetPromise) throw new Error("MathJax did not start");

        let alpha = null;
        for (let attempt = 0; attempt < 50 && !alpha; attempt += 1) {
          alpha = nodeElement("alpha");
          if (!alpha) await delay(20);
        }
        if (!alpha) throw new Error("graph node did not render");
        alpha.dispatchEvent(new MouseEvent("mouseenter", {bubbles: true, clientX: 200, clientY: 200}));
        await delay(150);
        if (document.getElementById("tooltip").querySelectorAll("mjx-container").length !== 2) {
          throw new Error("tooltip math was not typeset");
        }

        alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
        await delay(350);
        if (document.getElementById("details").querySelectorAll("mjx-container").length !== 2) {
          throw new Error("inspector math was not typeset");
        }
        """,
        virtual_time_budget=12000,
        wait_for_load=False,
    )


def test_mathjax_diagnostics_waits_for_a_stable_dynamic_typeset_tail() -> None:
    payload = _payload()
    payload["renderer_dependencies"] = [{"id": "mathjax", "version": "3"}]
    payload["entities"][0]["description"] = r"First dynamic value: $x$."
    payload["entities"][0]["connects_to"][0]["description"] = (
        r"Second dynamic value: $y$."
    )

    _run_browser_case(
        "mathjax-stable-typeset-tail",
        payload,
        """
        if (!window.MathJax?.startup?.promise) throw new Error("MathJax did not start");
        await window.MathJax.startup.promise;
        let alpha = null;
        let edgePath = null;
        for (let attempt = 0; attempt < 50 && (!alpha || !edgePath); attempt += 1) {
          alpha = nodeElement("alpha");
          edgePath = document.querySelector(".edge-path");
          if (!alpha || !edgePath) await delay(20);
        }
        if (!alpha || !edgePath) throw new Error("graph elements did not render");
        await window.officinaMathDiagnostics();

        const originalTypesetPromise = window.MathJax.typesetPromise;
        let controlledCallCount = 0;
        let releaseFirst;
        let releaseSecond;
        let markFirstStarted;
        let markSecondStarted;
        const firstGate = new Promise(resolve => { releaseFirst = resolve; });
        const secondGate = new Promise(resolve => { releaseSecond = resolve; });
        const firstStarted = new Promise(resolve => { markFirstStarted = resolve; });
        const secondStarted = new Promise(resolve => { markSecondStarted = resolve; });

        window.MathJax.typesetPromise = elements => {
          controlledCallCount += 1;
          if (controlledCallCount === 1) {
            markFirstStarted();
            return firstGate;
          }
          if (controlledCallCount === 2) {
            markSecondStarted();
            return secondGate.then(() => {
              window.__unresolvedTeX = window.__unresolvedTeX || {};
              window.__unresolvedTeX.MissingTail = true;
            });
          }
          return originalTypesetPromise.call(window.MathJax, elements);
        };

        try {
          alpha.dispatchEvent(new MouseEvent("mouseenter", {
            bubbles: true,
            clientX: 200,
            clientY: 200,
          }));
          await firstStarted;

          let diagnosticsSettled = false;
          const diagnosticsPromise = window.officinaMathDiagnostics().then(value => {
            diagnosticsSettled = true;
            return value;
          });
          await Promise.resolve();

          edgePath.dispatchEvent(new MouseEvent("click", {bubbles: true}));
          releaseFirst();
          await secondStarted;
          if (diagnosticsSettled) {
            throw new Error("diagnostics returned before the appended typeset tail");
          }

          releaseSecond();
          const diagnostics = await diagnosticsPromise;
          if (diagnostics.unresolvedCommands.join(",") !== "MissingTail") {
            throw new Error(
              "diagnostics missed the appended typeset result: " +
              diagnostics.unresolvedCommands.join(",")
            );
          }
        } finally {
          releaseFirst();
          releaseSecond();
          window.MathJax.typesetPromise = originalTypesetPromise;
        }
        """,
        virtual_time_budget=12000,
        wait_for_load=False,
    )


def test_mathjax_normalizes_both_macro_tuple_orders_to_semantic_mathml() -> None:
    payload = _payload()
    payload["renderer_dependencies"] = [
        {
            "id": "mathjax",
            "version": "3",
            "configuration": {
                "macros": {
                    "PairNative": ["#1+#2", 2],
                    "PairLegacy": [2, "#1+#2"],
                    "NestedPair": [r"\PairNative{#1}{#2}", 2],
                }
            },
        }
    ]
    payload["entities"][0]["description"] = (
        r"$\PairNative{a}{b}$ $\PairLegacy{a}{b}$ $\NestedPair{a}{b}$"
    )

    _run_browser_case(
        "mathjax-schema-macro-tuples",
        payload,
        """
        if (!window.MathJax?.startup?.promise) throw new Error("MathJax did not start");
        await window.MathJax.startup.promise;

        let alpha = null;
        for (let attempt = 0; attempt < 50 && !alpha; attempt += 1) {
          alpha = nodeElement("alpha");
          if (!alpha) await delay(20);
        }
        if (!alpha) throw new Error("graph node did not render");
        alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
        await delay(350);
        const diagnostics = await window.officinaMathDiagnostics();

        const math = Array.from(document.querySelectorAll("#details mjx-assistive-mml math"));
        if (math.length !== 3) {
          const containers = document.querySelectorAll("#details mjx-container").length;
          throw new Error("expected three semantic MathML expressions; got " + math.length +
            " from " + containers + " containers");
        }
        const expressions = math.map(node => (node.textContent || "").replace(/\\s+/g, ""));
        if (expressions.some(value => value !== "a+b")) {
          throw new Error("macro tuple semantics were not a+b: " + expressions.join(" | "));
        }
        if (diagnostics.mathErrorCount !== 0 || document.querySelector("mjx-merror")) {
          throw new Error("valid macro tuples produced a MathJax error");
        }
        if (diagnostics.unresolvedCommands.length ||
            document.querySelector("[data-unresolved-tex]")) {
          throw new Error("valid macro tuples were reported as unresolved");
        }
        """,
        virtual_time_budget=12000,
        wait_for_load=False,
    )


def test_mathjax_pairs_single_dollars_without_whitespace_or_currency_filters() -> None:
    payload = _payload()
    payload["renderer_dependencies"] = [
        {
            "id": "mathjax",
            "version": "3",
            "configuration": {
                "macros": {
                    "SpacedCanopy": r"\mathsf{W}",
                    "PairedSprout": r"\mathsf{C}",
                }
            },
        }
    ]
    payload["entities"][0]["description"] = (
        r"spaced $ \SpacedCanopy $ and currency-like $5+\PairedSprout$10"
    )

    _run_browser_case(
        "mathjax-paired-single-dollars",
        payload,
        """
        if (!window.MathJax?.startup?.promise) throw new Error("MathJax did not start");
        await window.MathJax.startup.promise;

        let alpha = null;
        for (let attempt = 0; attempt < 50 && !alpha; attempt += 1) {
          alpha = nodeElement("alpha");
          if (!alpha) await delay(20);
        }
        if (!alpha) throw new Error("graph node did not render");
        alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
        await delay(350);
        const diagnostics = await window.officinaMathDiagnostics();
        const expressions = Array.from(
          document.querySelectorAll("#details mjx-assistive-mml math")
        ).map(node => (node.textContent || "").replace(/\\s+/g, ""));
        if (!expressions.some(value => value.includes("W"))) {
          throw new Error("whitespace-delimited macro did not typeset");
        }
        if (!expressions.some(value => value.includes("5+C"))) {
          throw new Error("currency-shaped paired dollars did not typeset");
        }
        if (diagnostics.mathErrorCount !== 0 || diagnostics.unresolvedCommands.length) {
          throw new Error("paired-dollar macros produced diagnostics");
        }
        """,
        virtual_time_budget=12000,
        wait_for_load=False,
    )


def test_mathjax_reports_direct_and_nested_unknown_control_sequences() -> None:
    payload = _payload()
    payload["renderer_dependencies"] = [
        {
            "id": "mathjax",
            "version": "3",
            "configuration": {
                "macros": {
                    "NestedUnknown": [r"\MissingNested{#1}", 1],
                }
            },
        }
    ]
    payload["entities"][0]["description"] = (
        r"$\MissingDirect{x}$ $\NestedUnknown{x}$"
    )

    _run_browser_case(
        "mathjax-unknown-control-sequences",
        payload,
        """
        if (!window.MathJax?.startup?.promise) throw new Error("MathJax did not start");
        await window.MathJax.startup.promise;

        let alpha = null;
        for (let attempt = 0; attempt < 50 && !alpha; attempt += 1) {
          alpha = nodeElement("alpha");
          if (!alpha) await delay(20);
        }
        if (!alpha) throw new Error("graph node did not render");
        alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
        await delay(350);
        const diagnostics = await window.officinaMathDiagnostics();
        const unresolved = diagnostics.unresolvedCommands.slice().sort().join(",");
        if (unresolved !== "MissingDirect,MissingNested") {
          throw new Error("unknown-command oracle returned: " + unresolved);
        }
        if (diagnostics.mathErrorCount === 0) {
          throw new Error("unknown commands did not produce a MathJax error node");
        }
        const banner = document.querySelector("[data-unresolved-tex]");
        if (!banner) throw new Error("unknown-command banner is missing");
        const bannerNames = banner.dataset.unresolvedTex.split(",").sort().join(",");
        if (bannerNames !== unresolved) throw new Error("unknown-command banner is stale");
        """,
        virtual_time_budget=12000,
        wait_for_load=False,
    )


def test_mathjax_unknown_command_oracle_accepts_supported_primitives() -> None:
    payload = _payload()
    payload["renderer_dependencies"] = [{"id": "mathjax", "version": "3"}]
    payload["entities"][0]["description"] = (
        r"$\mathfrak{g}$ $\underbrace{x}_{u}$ $\overset{v}{y}$"
    )

    _run_browser_case(
        "mathjax-supported-primitives",
        payload,
        """
        if (!window.MathJax?.startup?.promise) throw new Error("MathJax did not start");
        await window.MathJax.startup.promise;

        let alpha = null;
        for (let attempt = 0; attempt < 50 && !alpha; attempt += 1) {
          alpha = nodeElement("alpha");
          if (!alpha) await delay(20);
        }
        if (!alpha) throw new Error("graph node did not render");
        alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
        await delay(350);
        const diagnostics = await window.officinaMathDiagnostics();

        const math = Array.from(document.querySelectorAll("#details mjx-assistive-mml math"));
        if (math.length !== 3) throw new Error("supported primitives did not produce MathML");
        if (math[0].querySelector('[mathvariant="fraktur"]')?.textContent !== "g") {
          throw new Error("mathfrak semantics are missing");
        }
        if (!math[1].querySelector("munder") || !math[1].textContent.includes("u")) {
          throw new Error("underbrace semantics are missing");
        }
        if (!math[2].querySelector("mover") || !math[2].textContent.includes("v")) {
          throw new Error("overset semantics are missing");
        }
        if (diagnostics.mathErrorCount !== 0 || document.querySelector("mjx-merror")) {
          throw new Error("supported primitives produced a MathJax error");
        }
        if (diagnostics.unresolvedCommands.length ||
            document.querySelector("[data-unresolved-tex]")) {
          throw new Error("supported primitives were reported as unresolved");
        }
        """,
        virtual_time_budget=12000,
        wait_for_load=False,
    )


def test_self_contained_canonical_graph_renders_all_dynamic_math_semantics() -> None:
    payload = json.loads(_task5_math_dependency_graph().read_text(encoding="utf-8"))

    _run_browser_case(
        "self-contained-canonical-graph",
        payload,
        """
        if (!window.MathJax?.startup?.promise) throw new Error("MathJax did not start");
        await window.MathJax.startup.promise;

        const expectedExpressionVariants = new Map([
          ["notation-root-set", {expressionIndex: 0, variants: ["script", "bold-italic"]}],
          ["notation-qtc-boundary", {expressionIndex: 1, variants: ["bold-italic"]}],
          ["construction-banach-structure", {expressionIndex: 0, variants: ["bold-italic"]}],
        ]);
        const assertSvgMathOutput = owner => {
          const containers = Array.from(details.querySelectorAll("mjx-container"));
          const semanticMath = details.querySelectorAll("mjx-assistive-mml math");
          if (containers.length !== semanticMath.length) {
            throw new Error(
              owner + " has " + semanticMath.length + " semantic math cases but " +
              containers.length + " MathJax containers"
            );
          }
          for (const container of containers) {
            if (!container.querySelector("svg")) {
              throw new Error(owner + " has a MathJax container without SVG output");
            }
          }
        };
        for (const entity of docData.entities) {
          showEntityDetails(entity);
          const diagnostics = await window.officinaMathDiagnostics();
          if (diagnostics.mathErrorCount !== 0 || details.querySelector("mjx-merror")) {
            const errors = Array.from(details.querySelectorAll("mjx-merror, merror"))
              .map(node => node.textContent || "").join(" | ");
            throw new Error(
              "MathJax error while rendering entity " + entity.id + ": " +
              diagnostics.unresolvedCommands.join(",") + " " + errors
            );
          }
          assertSvgMathOutput("entity " + entity.id);
          const expectation = expectedExpressionVariants.get(entity.id);
          if (expectation) {
            const expressions = details.querySelectorAll("mjx-assistive-mml math");
            const affectedExpression = expressions[expectation.expressionIndex];
            if (!affectedExpression) {
              throw new Error("representative entity lacks semantic MathML: " + entity.id);
            }
            for (const variant of expectation.variants) {
              if (!affectedExpression.querySelector(`[mathvariant="${variant}"]`)) {
                const observed = Array.from(
                  affectedExpression.querySelectorAll("[mathvariant]")
                ).map(node => node.getAttribute("mathvariant"));
                throw new Error(
                  `affected expression in ${entity.id} lacks ${variant} MathML semantics; ` +
                  `observed ${observed.join(",")}`
                );
              }
            }
          }
        }

        for (const entity of docData.entities) {
          for (const relation of entity.connects_to || []) {
            const edge = {
              ...relation,
              source: entity.id,
              target: relation.to,
            };
            showEdgeDetails(edge);
            const diagnostics = await window.officinaMathDiagnostics();
            if (diagnostics.mathErrorCount !== 0 || details.querySelector("mjx-merror")) {
              const errors = Array.from(details.querySelectorAll("mjx-merror, merror"))
                .map(node => node.textContent || "").join(" | ");
              throw new Error(
                "MathJax error while rendering edge " + entity.id + " -> " + relation.to +
                ": " + diagnostics.unresolvedCommands.join(",") + " " + errors
              );
            }
            assertSvgMathOutput("edge " + entity.id + " -> " + relation.to);
          }
        }

        const diagnostics = await window.officinaMathDiagnostics();
        if (diagnostics.unresolvedCommands.length) {
          throw new Error(
            "canonical graph has unresolved commands: " +
            diagnostics.unresolvedCommands.join(",")
          );
        }
        if (document.querySelector("[data-unresolved-tex]")) {
          throw new Error("canonical graph produced an unresolved-TeX marker");
        }
        """,
        virtual_time_budget=30000,
        wait_for_load=False,
    )


def test_structured_inspector_reuses_entity_description_when_summary_is_omitted() -> None:
    payload = _payload()
    payload["entities"][0]["description"] = "Canonical statement appears once."
    payload["entities"][0]["details"] = {
        "sections": [
            {
                "title": "Source",
                "fields": [{"label": "Location", "value": "main.tex:4", "format": "path"}],
            }
        ]
    }

    _run_browser_case(
        "structured-description-fallback",
        payload,
        """
        const alpha = nodeElement("alpha");
        if (!alpha) throw new Error("graph node did not render");
        alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
        await delay(350);
        const detailText = document.getElementById("details").textContent;
        if (!detailText.includes("Canonical statement appears once.")) {
          throw new Error("structured inspector did not reuse entity description");
        }
        if (!detailText.includes("main.tex:4")) throw new Error("source section is missing");
        """,
    )


def test_node_and_color_legend_headings_toggle_independently() -> None:
    _run_browser_case(
        "collapsible-node-color-legends",
        _payload(),
        """
        const nodes = document.querySelector('details[data-legend-section="nodes"]');
        const colors = document.querySelector('details[data-legend-section="colors"]');
        if (!nodes || !colors) throw new Error("node or color disclosure is missing");
        if (!nodes.open || !colors.open) throw new Error("legend disclosures did not start open");

        const nodesSummary = nodes.querySelector("summary");
        const colorsSummary = colors.querySelector("summary");
        const nodeRow = nodes.querySelector('.legend-row[data-legend-kind="node"][data-type="node"]');
        const colorRow = colors.querySelector('.legend-row[data-legend-kind="node"][data-legend-facet="kind"][data-type="node"]');
        if (!nodesSummary || nodesSummary.textContent.trim() !== "Nodes" || !nodeRow) {
          throw new Error("Nodes disclosure content is incomplete");
        }
        if (!colorsSummary || colorsSummary.textContent.trim() !== "Colors" || !colorRow) {
          throw new Error("Colors disclosure content is incomplete");
        }

        nodesSummary.click();
        if (nodes.open || !colors.open) throw new Error("Nodes did not collapse independently");
        nodesSummary.click();
        if (!nodes.open || !colors.open) throw new Error("Nodes did not reopen independently");
        nodeRow.click();
        await delay(20);
        if (selectedNodeIds.size !== 2) throw new Error("node legend selection broke after reopening");

        setNodeSelection([], null, "explicit");
        colorsSummary.click();
        if (colors.open || !nodes.open) throw new Error("Colors did not collapse independently");
        colorsSummary.click();
        if (!colors.open || !nodes.open) throw new Error("Colors did not reopen independently");
        colorRow.click();
        await delay(20);
        if (selectedNodeIds.size !== 2) throw new Error("color legend selection broke after reopening");
        """,
    )


def test_edge_occlusion_masks_follow_nonrectangular_node_shapes() -> None:
    shapes = ["ellipse", "circle", "diamond", "hexagon", "parallelogram"]
    payload = {
        "schema_version": 2,
        "graph_id": "shape-aware-edge-occlusion",
        "categories": [
            {"id": shape, "label": shape.title(), "shape": shape}
            for shape in shapes
        ],
        "edge_categories": [{"id": "link", "label": "Link"}],
        "entities": [
            {
                "id": shape,
                "type": shape,
                "category": shape,
                "short_title": shape.title(),
                "position": index,
                "connects_to": [],
            }
            for index, shape in enumerate(shapes)
        ],
    }
    _run_browser_case(
        "shape-aware-edge-occlusion",
        payload,
        """
        const positions = Array.from(lastNodePositions.values());
        const left = Math.min(...positions.map(position => position.x)) - 20;
        const top = Math.min(...positions.map(position => position.y)) - 20;
        const right = Math.max(...positions.map(position => position.x + position.width)) + 20;
        const bottom = Math.max(...positions.map(position => position.y + position.height)) + 20;
        const probe = createSvgElement("path");
        probe.setAttribute("class", "edge-path");
        probe.setAttribute("d", `M ${left} ${top} H ${right} V ${bottom} H ${left} Z`);
        probe.dataset.sourceNodeId = "outside-source";
        probe.dataset.targetNodeId = "outside-target";
        edgeLayer.appendChild(probe);
        refreshEdgeOcclusionMasks();

        const maskReference = probe.getAttribute("mask") || "";
        const maskId = maskReference.startsWith("url(#") ? maskReference.slice(5, -1) : "";
        const mask = maskId ? document.getElementById(maskId) : null;
        if (!mask) throw new Error("probe edge did not receive an occlusion mask");
        for (const nodeId of ["ellipse", "circle", "diamond", "hexagon", "parallelogram"]) {
          const visibleShape = nodeElement(nodeId)?.querySelector(".node-shape");
          const blocker = mask.querySelector(`[data-edge-occlusion-node-id="${nodeId}"]`);
          if (!visibleShape || !blocker) throw new Error(`${nodeId} blocker is missing`);
          if (blocker.tagName !== visibleShape.tagName) {
            throw new Error(`${nodeId} uses ${blocker.tagName} occlusion for a ${visibleShape.tagName} node`);
          }
          for (const attribute of ["x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "points"]) {
            if (visibleShape.hasAttribute(attribute)
                && blocker.getAttribute(attribute) !== visibleShape.getAttribute(attribute)) {
              throw new Error(`${nodeId} blocker changed its ${attribute} geometry`);
            }
          }
        }
        """,
    )


def test_selected_node_buttons_switch_primary_inspector_without_losing_selection() -> None:
    _run_browser_case(
        "selection-inspector-navigation",
        _payload(),
        """
        const alpha = nodeElement("alpha");
        const beta = nodeElement("beta");
        alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
        await delay(220);
        beta.dispatchEvent(new MouseEvent("click", {bubbles: true, ctrlKey: true}));
        await delay(220);
        if (selectedNodeIds.size !== 2 || selectedNodeId !== "beta") {
          throw new Error("multi-selection setup failed");
        }
        const buttons = Array.from(document.querySelectorAll("[data-selection-node-id]"));
        if (buttons.length !== 2) throw new Error("selected-node navigator is incomplete");
        const alphaButton = buttons.find(button => button.dataset.selectionNodeId === "alpha");
        if (!alphaButton || alphaButton.tagName !== "BUTTON") {
          throw new Error("selected node is not a native button");
        }
        alphaButton.click();
        await delay(220);
        if (selectedNodeIds.size !== 2 || selectedNodeId !== "alpha") {
          throw new Error("inspector navigation changed the selection set");
        }
        if (!document.getElementById("details").textContent.includes("Alpha description")) {
          throw new Error("inspector did not switch descriptions");
        }
        if (!alpha.classList.contains("primary-selected") || beta.classList.contains("primary-selected")) {
          throw new Error("primary graph highlight did not move");
        }
        document.getElementById("visibility-undo-btn").click();
        await delay(20);
        if (selectedNodeIds.size !== 2 || selectedNodeId !== "beta") {
          throw new Error("undo did not restore the previous primary node");
        }
        const refreshedAlphaButton = Array.from(document.querySelectorAll("[data-selection-node-id]"))
          .find(button => button.dataset.selectionNodeId === "alpha");
        refreshedAlphaButton.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
        await delay(220);
        if (selectedNodeIds.has("alpha") || selectedNodeIds.size !== 1 || selectedNodeId !== "beta") {
          throw new Error("double-click did not remove only the chosen selected node");
        }
        setNodeSelection(["alpha", "beta"], "beta", "explicit");
        const focusedSelectionButton = Array.from(document.querySelectorAll("[data-selection-node-id]"))
          .find(button => button.dataset.selectionNodeId === "alpha");
        focusedSelectionButton.focus();
        focusedSelectionButton.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
        await delay(20);
        if (selectedNodeIds.size !== 0 || selectedNodeId !== null) {
          throw new Error("Escape did not clear selection while a selection button was focused");
        }
        setNodeSelection(["alpha", "beta"], "beta", "explicit");
        document.getElementById("filter-clear").click();
        await delay(20);
        if (selectedNodeIds.size !== 0 || selectedNodeId !== null) {
          throw new Error("Clear did not clear the complete selection");
        }
        """,
    )


def test_relation_direction_controls_select_union_and_color_legend_selects_kind() -> None:
    payload = {
        "schema_version": 2,
        "graph_id": "relation-selection-controls",
        "categories": [
            {"id": "source", "label": "Source"},
            {"id": "source:python", "label": "Python", "parent": "source", "color": "#00a67d"},
            {"id": "source:markdown", "label": "Markdown", "parent": "source", "color": "#d35400"},
            {"id": "source:python+markdown", "label": "Python + Markdown", "parent": "source"},
        ],
        "edge_categories": [{"id": "link", "label": "Link"}],
        "entities": [
            {"id": "a", "type": "source", "kind": "python", "category": "source:python", "short_title": "A", "position": 0, "connects_to": [{"to": "b", "type": "link"}]},
            {"id": "c", "type": "source", "kind": "python", "category": "source:python", "short_title": "C", "position": 1, "connects_to": [{"to": "b", "type": "link"}]},
            {"id": "b", "type": "source", "kind": "markdown", "category": "source:markdown", "short_title": "B", "position": 2, "connects_to": [{"to": "d", "type": "link"}]},
            {"id": "d", "type": "source", "kind": "markdown", "category": "source:markdown", "short_title": "D", "position": 3, "connects_to": []},
            {"id": "mixed", "type": "source", "kind": "python+markdown", "category": "source:python+markdown", "short_title": "Mixed", "position": 4, "connects_to": []},
        ],
    }
    _run_browser_case(
        "relation-selection-controls",
        payload,
        """
        if (document.querySelector(".filter-advanced")) throw new Error("advanced node filters remain");
        const pythonColor = document.querySelector('.legend-row[data-legend-kind="node"][data-legend-facet="kind"][data-type="python"]');
        if (!pythonColor) throw new Error("generic color legend is missing");
        if (document.querySelector('.legend-row[data-legend-kind="node"][data-legend-facet="kind"][data-type="python+markdown"]')) {
          throw new Error("mixed kind received a redundant color legend entry");
        }
        const mixedFill = nodeElement("mixed").querySelector(".node-shape").getAttribute("fill") || "";
        if (!mixedFill.startsWith("url(#node-kind-gradient-")) throw new Error("mixed kind did not receive a component gradient");
        pythonColor.click();
        await delay(20);
        if (selectedNodeIds.size !== 3 || !selectedNodeIds.has("a") || !selectedNodeIds.has("c") || !selectedNodeIds.has("mixed")) {
          throw new Error("color legend did not select matching nodes");
        }
        const successor = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="link"] .relation-traverse-button[data-direction="successors"]');
        const ancestor = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="link"] .relation-traverse-button[data-direction="ancestors"]');
        if (!successor || !ancestor || successor.disabled || ancestor.disabled) throw new Error("relation traversal controls unavailable");
        const primaryBefore = selectedNodeId;
        successor.click();
        await delay(20);
        if (selectedNodeIds.size !== 5 || !selectedNodeIds.has("b") || !selectedNodeIds.has("d")) {
          throw new Error("successor traversal did not add the transitive union");
        }
        if (selectedNodeId !== primaryBefore) throw new Error("relation traversal changed the primary node");
        setNodeSelection(["b"], "b", "explicit");
        ancestor.click();
        await delay(20);
        if (selectedNodeIds.size !== 3 || !selectedNodeIds.has("a") || !selectedNodeIds.has("b") || !selectedNodeIds.has("c")) {
          throw new Error("ancestor traversal did not add the incoming union");
        }
        document.getElementById("visibility-undo-btn").click();
        await delay(20);
        if (selectedNodeIds.size !== 1 || selectedNodeId !== "b") throw new Error("traversal selection is not undoable");
        """,
    )


def test_relations_heading_traverses_all_visible_relation_types() -> None:
    payload = {
        "schema_version": 2,
        "graph_id": "all-relation-selection-controls",
        "categories": [{"id": "node", "label": "Node"}],
        "edge_categories": [
            {"id": "first", "label": "First"},
            {"id": "second", "label": "Second"},
        ],
        "entities": [
            {
                "id": "a",
                "type": "node",
                "short_title": "A",
                "position": 0,
                "connects_to": [{"to": "b", "type": "first"}],
            },
            {
                "id": "b",
                "type": "node",
                "short_title": "B",
                "position": 1,
                "connects_to": [{"to": "c", "type": "second"}],
            },
            {
                "id": "d",
                "type": "node",
                "short_title": "D",
                "position": 2,
                "connects_to": [{"to": "b", "type": "second"}],
            },
            {
                "id": "c",
                "type": "node",
                "short_title": "C",
                "position": 3,
                "connects_to": [],
            },
        ],
    }
    _run_browser_case(
        "all-relation-selection-controls",
        payload,
        """
        const relationsTitle = document.querySelector('.legend-group-title[data-legend-kind="relations"]');
        const successor = relationsTitle?.querySelector('.relation-traverse-button[data-direction="successors"]');
        const ancestor = relationsTitle?.querySelector('.relation-traverse-button[data-direction="ancestors"]');
        if (!successor || !ancestor) throw new Error("all-relation traversal controls unavailable");
        if (!successor.disabled || !ancestor.disabled) throw new Error("all-relation controls enabled without a selection");

        setNodeSelection(["a"], "a", "explicit");
        if (successor.disabled || ancestor.disabled) throw new Error("all-relation controls disabled with a selection");
        successor.click();
        await delay(20);
        if (selectedNodeIds.size !== 3 || !selectedNodeIds.has("a") || !selectedNodeIds.has("b") || !selectedNodeIds.has("c")) {
          throw new Error("all-relation successor traversal did not cross relation types");
        }
        if (selectedNodeId !== "a") throw new Error("all-relation traversal changed the primary node");

        setNodeSelection(["c"], "c", "explicit");
        ancestor.click();
        await delay(20);
        if (selectedNodeIds.size !== 4 || !selectedNodeIds.has("a") || !selectedNodeIds.has("b") || !selectedNodeIds.has("c") || !selectedNodeIds.has("d")) {
          throw new Error("all-relation ancestor traversal did not union incoming relation types");
        }
        document.getElementById("visibility-undo-btn").click();
        await delay(20);
        if (selectedNodeIds.size !== 1 || selectedNodeId !== "c") throw new Error("all-relation traversal selection is not undoable");
        """,
    )


def test_advanced_edge_geometries_are_distinct_and_reversible() -> None:
    _run_browser_case(
        "advanced-bezier-geometry",
        _payload("binds-interface"),
        """
        const geometry = document.getElementById("routing-geometry");
        const routingControls = document.getElementById("routing-controls");
        if (!geometry || !routingControls || routingControls.querySelector("details")) {
          throw new Error("routing controls retain a nested disclosure");
        }
        const groupTitles = Array.from(routingControls.querySelectorAll(".routing-group-title"))
          .map(title => title.textContent.trim());
        for (const title of ["Presets", "Edge geometry", "Layout spacing"]) {
          if (!groupTitles.includes(title)) throw new Error(`${title} group missing`);
        }
        const path = document.querySelector(
          '.edge-path[data-source-node-id="alpha"][data-target-node-id="beta"]'
        );
        const optionValues = Array.from(geometry.options).map(option => option.value);
        for (const required of ["orthogonal", "polyline", "spline", "bezier", "straight"]) {
          if (!optionValues.includes(required)) throw new Error(`${required} geometry option missing`);
        }
        const setGeometry = value => {
          geometry.value = value;
          geometry.dispatchEvent(new Event("change", {bubbles: true}));
          return path.getAttribute("d") || "";
        };
        const row = id => document.getElementById(id).closest(".routing-row");
        const expectRows = (visible, hidden) => {
          for (const id of visible) {
            if (row(id).hidden) throw new Error(`${id} should be visible`);
          }
          for (const id of hidden) {
            if (!row(id).hidden) throw new Error(`${id} should be hidden`);
          }
        };
        const orthogonal = setGeometry("orthogonal");
        expectRows(
          ["routing-radius", "routing-merge"],
          ["routing-polyline-bend", "routing-spline-tension", "routing-bezier-curvature"]
        );
        const polyline = setGeometry("polyline");
        expectRows(
          ["routing-polyline-bend"],
          ["routing-radius", "routing-merge", "routing-spline-tension", "routing-bezier-curvature"]
        );
        const polylineBend = document.getElementById("routing-polyline-bend");
        polylineBend.value = "75";
        polylineBend.dispatchEvent(new Event("input", {bubbles: true}));
        if (path.getAttribute("d") === polyline) throw new Error("polyline bend has no effect");
        const spline = setGeometry("spline");
        expectRows(
          ["routing-spline-tension"],
          ["routing-radius", "routing-merge", "routing-polyline-bend", "routing-bezier-curvature"]
        );
        const splineTension = document.getElementById("routing-spline-tension");
        const splineBeforeTension = path.getAttribute("d");
        splineTension.value = "40";
        splineTension.dispatchEvent(new Event("input", {bubbles: true}));
        if (path.getAttribute("d") === splineBeforeTension) throw new Error("spline tension has no effect");
        const bezier = setGeometry("bezier");
        expectRows(
          ["routing-bezier-curvature"],
          ["routing-radius", "routing-merge", "routing-polyline-bend", "routing-spline-tension"]
        );
        const bezierCurvature = document.getElementById("routing-bezier-curvature");
        const bezierBeforeCurvature = path.getAttribute("d");
        bezierCurvature.value = "70";
        bezierCurvature.dispatchEvent(new Event("input", {bubbles: true}));
        if (path.getAttribute("d") === bezierBeforeCurvature) throw new Error("Bezier curvature has no effect");
        const straight = setGeometry("straight");
        expectRows(
          [],
          ["routing-radius", "routing-merge", "routing-polyline-bend", "routing-spline-tension", "routing-bezier-curvature"]
        );
        for (const id of ["routing-clearance", "routing-parallel", "routing-node-spacing", "routing-layer-spacing", "routing-edge-node-spacing"]) {
          const shouldHide = id === "routing-parallel";
          if (row(id).hidden !== shouldHide) throw new Error(`${id} has incorrect relevance visibility`);
        }
        if (document.getElementById("routing-shape")) throw new Error("redundant shape preset remains");
        if (orthogonal.includes("C")) throw new Error("orthogonal path became cubic");
        if ((polyline.match(/ L /g) || []).length < 2 || polyline.includes("C")) {
          throw new Error("polyline option did not produce segmented lines");
        }
        if ((spline.match(/ C /g) || []).length < 2) {
          throw new Error("spline option did not produce a multi-segment curve");
        }
        if ((bezier.match(/ C /g) || []).length !== 1) {
          throw new Error("Bezier option did not produce one cubic curve");
        }
        if ((straight.match(/ L /g) || []).length !== 1 || /[CQ]/.test(straight)) {
          throw new Error("straight option did not produce one line");
        }
        const target = getEffectivePos("beta");
        const source = getEffectivePos("alpha");
        manualPositions.set("beta", {...target, x: target.x + 120, y: target.y + 60});
        setGeometry("spline");
        rerouteIncidentEdgesFromCurrentPositions("beta");
        if (((path.getAttribute("d") || "").match(/ C /g) || []).length < 2) {
          throw new Error("drag rerouting lost spline geometry");
        }
        const coordinatesAfterMove = (path.getAttribute("d") || "").match(/-?\\d+(?:\\.\\d+)?/g).map(Number);
        const startX = coordinatesAfterMove[0];
        const startY = coordinatesAfterMove[1];
        const sourceBoundaryAttached =
          ((Math.abs(startX - source.x) < 0.01 || Math.abs(startX - source.x - source.width) < 0.01) && startY >= source.y && startY <= source.y + source.height) ||
          ((Math.abs(startY - source.y) < 0.01 || Math.abs(startY - source.y - source.height) < 0.01) && startX >= source.x && startX <= source.x + source.width);
        if (!sourceBoundaryAttached) throw new Error("moving an edge destination detached the source endpoint");
        const restored = setGeometry("orthogonal");
        if (routingConfig.geometry !== "orthogonal" || restored.includes("C")) {
          throw new Error("orthogonal geometry was not restored");
        }
        """,
    )
