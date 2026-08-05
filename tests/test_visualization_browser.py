from pathlib import Path
import os
import shutil
import subprocess
import tempfile

import pytest

from officina.common.visualization.elk_html_renderer import build_html_with_elk


def test_filter_interactions_keep_layout_and_explain_projection():
    chrome = shutil.which("google-chrome")
    if chrome is None:
        # famulus-skip: category=capability-unavailable; reason=Google Chrome is not installed; alternate=renderer contract tests cover payload and HTML generation
        pytest.skip("google-chrome unavailable")
    doc = {
        "schema_version": 2,
        "graph_id": "temporary-filter-smoke",
        "categories": [
            {"id": "element", "label": "Element"},
            {"id": "endpoint", "label": "Endpoint", "parent": "element"},
            {"id": "artifact", "label": "Artifact", "parent": "element"},
        ],
        "edge_categories": [
            {"id": "dependency", "label": "Dependency"},
            {"id": "uses", "label": "Uses", "parent": "dependency"},
            {"id": "indirect", "label": "Indirect dependency"},
        ],
        "relation_semantics": {
            "transformations": {"node_omission": {
                "rules": [{
                    "id": "hidden-dependency",
                    "causes": ["user-hidden"],
                    "left_types": ["dependency", "indirect"],
                    "right_types": ["dependency"],
                    "outcomes": [{"type": "indirect", "fidelity": "exact"}],
                }]
            }},
            "subsumptions": [],
        },
        "detail_levels": [
            {"id": "overview", "label": "Overview"},
            {"id": "component", "label": "Components"},
            {"id": "item", "label": "Items"},
        ],
        "ui": {
            "layout": {"rankdir": "LR", "aspect_ratio": 1.7},
            "presentation_node_controls": [{
                "id": "skill-grouping", "label": "Skill grouping",
                "selector_label": "Group skills by", "default_facet": None,
                "facets": [
                    {"id": "discovery.domain", "label": "Domain", "activation": "all", "node_ids": ["discovery.domain.research", "discovery.domain.software-development"]},
                    {"id": "discovery.topics", "label": "Topics", "activation": "multiple", "node_ids": ["discovery.topics.visualization", "discovery.topics.repository-workflow", "discovery.topics.planning", "discovery.topics.task-automation"]},
                    {"id": "discovery.activated_by", "label": "Activated by", "activation": "all", "node_ids": ["discovery.activated_by.user-request"]},
                    {"id": "discovery.persistent_modifier", "label": "Persistent modifier", "activation": "all", "node_ids": ["discovery.persistent_modifier.not-persistent"]},
                    {"id": "discovery.visibility", "label": "Catalog visibility", "activation": "all", "node_ids": ["discovery.visibility.listed"]},
                ],
            }],
            "edge_styles": {
                "dependency": {"color": "#b45309"},
                "uses": {"color": "#2563eb", "dash": "10 5"},
                "indirect": {"color": "#64748b", "dash": "3 5"},
            },
            "edge_metadata_styles": {
                "mixed_type_bundle": {
                    "label": "Mixed edge evidence",
                    "description": "Combines several semantic relation types in this test graph.",
                    "style": {"stroke_width": 4.25, "outline_width": 7.5, "outline_color": "#475569", "outline_opacity": 0.3},
                },
            },
        },
        "presentation_nodes": [
            {"id": "discovery.domain.research", "type": "group", "short_title": "Research", "position": 0, "member_ids": ["root", "gamma"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
            {"id": "discovery.domain.software-development", "type": "group", "short_title": "Software development", "position": 1, "member_ids": ["delta"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
            {"id": "discovery.topics.visualization", "type": "group", "short_title": "Visualization", "position": 2, "member_ids": ["root", "gamma"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
            {"id": "discovery.topics.repository-workflow", "type": "group", "short_title": "Repository workflow", "position": 3, "member_ids": ["root", "delta"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
            {"id": "discovery.topics.planning", "type": "group", "short_title": "Planning", "position": 4, "member_ids": ["gamma"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
            {"id": "discovery.topics.task-automation", "type": "group", "short_title": "Task automation", "position": 5, "member_ids": ["delta"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
            {"id": "discovery.activated_by.user-request", "type": "group", "short_title": "User request", "position": 6, "member_ids": ["root"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
            {"id": "discovery.persistent_modifier.not-persistent", "type": "group", "short_title": "Not a persistent modifier", "position": 7, "member_ids": ["root"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
            {"id": "discovery.visibility.listed", "type": "group", "short_title": "Listed", "position": 8, "member_ids": ["root"], "presentation": {"form": "supernode", "tone": "subtle", "default_visibility": "hidden"}, "interaction": {"selectable": True, "inspectable": True, "draggable": "members", "collapse_effect": "self"}},
        ],
        "entities": [
            {"id": "root", "type": "group", "detail_level": "overview", "kind": "composite", "category": "element", "short_title": "Root", "position": 0, "connects_to": [{"to": "alpha", "type": "dependency", "description": "owns execution"}]},
            {"id": "nested", "type": "group", "detail_level": "overview", "kind": "composite", "category": "element", "short_title": "Nested", "container": "root", "position": 1, "connects_to": []},
            {"id": "alpha", "type": "port", "detail_level": "item", "kind": "service", "category": "endpoint", "short_title": "Alpha API", "container": "nested", "position": 2, "connects_to": [{"to": "beta", "type": "uses", "description": "reads records"}, {"to": "beta", "type": "uses", "description": "updates records"}, {"to": "beta", "type": "dependency", "description": "requires storage"}]},
            {"id": "beta", "type": "record", "detail_level": "component", "kind": "data", "category": "artifact", "short_title": "Beta Source", "container": "nested", "position": 3, "connects_to": []},
            {"id": "odd\"node", "type": "record", "detail_level": "component", "kind": "service", "category": "artifact", "short_title": "Odd Node", "container": "nested", "position": 4, "connects_to": []},
            {"id": "zeta", "type": "record", "detail_level": "item", "kind": "service", "category": "artifact", "short_title": "Zeta Source", "container": "nested", "position": 5, "connects_to": [{"to": "alpha", "type": "dependency", "description": "feeds alpha", "confidence": "Likely"}, {"to": "alpha", "type": "dependency", "description": "validates alpha", "confidence": "Likely"}]},
            {"id": "gamma", "type": "group", "detail_level": "item", "kind": "service", "category": "artifact", "short_title": "Gamma skill", "position": 6, "connects_to": [{"to": "delta", "type": "dependency", "description": "cross-skill dependency"}]},
            {"id": "delta", "type": "group", "detail_level": "item", "kind": "data", "category": "artifact", "short_title": "Delta skill", "position": 7, "connects_to": []},
            {"id": "boundary", "type": "group", "detail_level": "item", "kind": "composite", "category": "element", "short_title": "Boundary proxy", "position": 8, "connects_to": []},
        ],
    }
    base_html = build_html_with_elk(doc)
    html = base_html.replace(
        "</body>",
        """<script>
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        const waitForLayout = async () => {
          for (let attempt = 0; attempt < 200; attempt += 1) {
            if (!document.getElementById("elk-status").textContent.includes("Rendering graph layout")) return;
            await delay(20);
          }
          throw new Error("layout did not settle before interaction assertion");
        };
        const fail = message => { document.body.dataset.testStatus = "FAIL:" + message; document.title = "FAIL:" + message; };
        const pass = () => { document.body.dataset.testStatus = "PASS"; document.title = "PASS"; };
        window.addEventListener("load", () => setTimeout(async () => {
          try {
            await waitForLayout();
            let alpha = document.querySelector('[data-node-id="alpha"]');
            let beta = document.querySelector('[data-node-id="beta"]');
            let root = document.querySelector('[data-node-id="root"]');
            if (!alpha || !beta || !root) throw new Error("initial nodes missing");
            const presentationFacet = document.getElementById("presentation-node-control-dimension");
            if (!presentationFacet || presentationFacet.value !== "") throw new Error("grouping did not default off");
            const groupingOptions = Array.from(presentationFacet.options).map(option => option.value);
            if (JSON.stringify(groupingOptions) !== JSON.stringify(["", "discovery.domain", "discovery.topics", "discovery.activated_by", "discovery.persistent_modifier", "discovery.visibility"])) throw new Error("grouping dimension menu is incomplete or out of order");
            setNodeSelection(["alpha", "beta"], "beta", "explicit");
            saveViewerState();
            selectedNodeIds.clear(); selectedNodeId = null;
            restoreViewerState();
            if (selectedNodeIds.size !== 2 || selectedNodeId !== "beta") throw new Error("version-7 viewer state lost multi-selection");
            deselect();
            const canonicalNodeCount = document.querySelectorAll(".graph-node").length;
            const canonicalEdgeCount = document.querySelectorAll(".edge-path").length;
            const alphaRootDelta = {
              x: lastNodePositions.get("alpha").x - lastNodePositions.get("root").x,
              y: lastNodePositions.get("alpha").y - lastNodePositions.get("root").y,
            };
            ["root", "nested", "alpha", "beta", 'odd"node', "zeta", "gamma", "delta", "boundary"].forEach(nodeId => {
              const position = lastNodePositions.get(nodeId);
              manualPositions.set(nodeId, {...position, x: position.x + 17, y: position.y + 9});
            });
            const retainedManualRootX = manualPositions.get("root").x;
            presentationFacet.value = "discovery.topics";
            presentationFacet.dispatchEvent(new Event("change", {bubbles: true}));
            const topicChoices = document.querySelectorAll('[data-presentation-node] input[type="checkbox"]');
            if (topicChoices.length !== 4 || Array.from(topicChoices).some(input => input.checked)) throw new Error("topics did not start as an empty multi-select");
            topicChoices.forEach(input => {
              input.checked = true;
              input.dispatchEvent(new Event("change", {bubbles: true}));
            });
            await waitForLayout();
            if (!manualPositions.has("root")) throw new Error(`grouped layout deleted retained manual positions: grouped=${Array.from(presentationGroupedNodeIds).join(",")}`);
            if (document.querySelectorAll(".graph-node").length !== canonicalNodeCount || document.querySelectorAll(".edge-path").length !== canonicalEdgeCount) throw new Error("grouping controls changed canonical graph content");
            const groupedDelta = {
              x: lastNodePositions.get("alpha").x - lastNodePositions.get("root").x,
              y: lastNodePositions.get("alpha").y - lastNodePositions.get("root").y,
            };
            if (groupedDelta.x !== alphaRootDelta.x || groupedDelta.y !== alphaRootDelta.y) throw new Error("grouping did not move the skill subtree as a rigid block");
            const presentationShells = document.querySelectorAll("#presentation-node-layer .presentation-node-component");
            if (presentationShells.length < 2) throw new Error("metadata shells missing");
            if (document.querySelectorAll("#presentation-node-layer .graph-node").length) throw new Error("metadata shell entered graph-node interactions");
            const visualizationShell = document.querySelector('[data-presentation-node-id="discovery.topics.visualization"]');
            visualizationShell.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            const hiddenTopicToggle = document.querySelector('[data-presentation-node="discovery.topics.visualization"] input');
            if (!hiddenPresentationNodes.has("discovery.topics.visualization") || hiddenTopicToggle.checked) throw new Error("hidden multiple-facet node did not expose a restore control");
            hiddenTopicToggle.checked = true;
            hiddenTopicToggle.dispatchEvent(new Event("change", {bubbles: true}));
            await waitForLayout();
            if (hiddenPresentationNodes.has("discovery.topics.visualization") || !document.querySelector('[data-presentation-node-id="discovery.topics.visualization"]')) throw new Error("multiple-facet node did not restore");
            const sharedRootBeforeDrag = {...lastNodePositions.get("root")};
            const overlappingTopicBeforeDrag = JSON.stringify(presentationNodeComponents.filter(component => component.presentationNodeId === "discovery.topics.repository-workflow").map(component => component.bounds));
            const draggableTopic = document.querySelector('[data-presentation-node-id="discovery.topics.visualization"]');
            const topicDragZoom = zoomLevel;
            draggableTopic.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0, clientX: 100, clientY: 100}));
            document.dispatchEvent(new MouseEvent("mousemove", {bubbles: true, clientX: 132, clientY: 116}));
            document.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, clientX: 132, clientY: 116}));
            await waitForLayout();
            const expectedSharedDx = 32 / topicDragZoom;
            if (Math.abs((lastNodePositions.get("root").x - sharedRootBeforeDrag.x) - expectedSharedDx) > 0.01) throw new Error("shared member moved more than once during presentation drag");
            const overlappingTopicAfterDrag = JSON.stringify(presentationNodeComponents.filter(component => component.presentationNodeId === "discovery.topics.repository-workflow").map(component => component.bounds));
            if (overlappingTopicAfterDrag === overlappingTopicBeforeDrag) throw new Error("overlapping presentation shell did not reshape after shared-member drag");
            presentationNodeOffsets.clear();
            await updateVisibilityFull({preserveManualPositions: true});
            if (nodeElement("root").getAttribute("data-node-id") !== "root") throw new Error("grouping duplicated canonical skill identity");
            const rootPosition = lastNodePositions.get("root");
            const containsPosition = (bound, position) => position.x >= bound.x && position.y >= bound.y && position.x + position.width <= bound.x + bound.width && position.y + position.height <= bound.y + bound.height;
            for (const presentationNodeId of ["discovery.topics.visualization", "discovery.topics.repository-workflow"]) {
              if (!presentationNodeComponents.some(component => component.presentationNodeId === presentationNodeId && containsPosition(component.bounds, rootPosition))) throw new Error("multi-member skill escaped selected shell " + presentationNodeId);
            }
            const boundaryPosition = lastNodePositions.get("boundary");
            if (presentationNodeComponents.some(component => containsPosition(component.bounds, boundaryPosition))) throw new Error("ungrouped root was falsely enclosed by a metadata shell");
            const movedCrossSkillEdge = document.querySelector('.edge-path[data-source-node-id="gamma"][data-target-node-id="delta"]');
            if (!movedCrossSkillEdge?.getAttribute("d")) throw new Error("edge between grouped roots was not rerouted");
            const groupedManualRootX = manualPositions.get("root").x;
            nodeElement("root").dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0, clientX: 100, clientY: 100}));
            document.dispatchEvent(new MouseEvent("mousemove", {bubbles: true, clientX: 140, clientY: 140}));
            document.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, clientX: 140, clientY: 140}));
            if (manualPositions.get("root").x !== groupedManualRootX) throw new Error("grouped drag mutated retained manual position");
            const groupingState = serializePresentationNodesState();
            if (groupingState.activeFacets["skill-grouping"] !== "discovery.topics" || groupingState.selectedNodeIds["discovery.topics"].length !== 4) throw new Error("topic selection did not serialize");
            restorePresentationNodesState({activeDimension: "discovery.topics", selectedValues: {"discovery.topics": ["missing", "visualization"]}, hiddenShells: []});
            if (selectedPresentationNodeIds.get("discovery.topics").has("discovery.topics.missing") || !selectedPresentationNodeIds.get("discovery.topics").has("discovery.topics.visualization")) throw new Error("v6 presentation state migration was invalid");
            presentationFacet.value = "discovery.domain";
            presentationFacet.dispatchEvent(new Event("change", {bubbles: true}));
            await waitForLayout();
            let researchShell = document.querySelector('[data-presentation-node-id="discovery.domain.research"]');
            researchShell.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
            if (selectedPresentationNodeId !== "discovery.domain.research" || !document.getElementById("details").textContent.includes("Research")) throw new Error("presentation node did not select and inspect");
            deselect();
            const persistedAfterPresentationDeselect = JSON.parse(localStorage.getItem(viewerStateKey));
            if (selectedPresentationNodeId !== null || persistedAfterPresentationDeselect.presentationNodes.selectedNodeId !== null) throw new Error("presentation deselect did not persist");
            undoGraphAction();
            if (selectedPresentationNodeId !== "discovery.domain.research") throw new Error("presentation deselect was not undoable");
            redoGraphAction();
            if (selectedPresentationNodeId !== null) throw new Error("presentation deselect redo did not clear selection");
            researchShell = document.querySelector('[data-presentation-node-id="discovery.domain.research"]');
            researchShell.dispatchEvent(new MouseEvent("click", {bubbles: true}));
            showEdgeDetails(edgeData[0]);
            const persistedAfterEdgeInspection = JSON.parse(localStorage.getItem(viewerStateKey));
            if (selectedPresentationNodeId !== null || persistedAfterEdgeInspection.presentationNodes.selectedNodeId !== null) throw new Error("edge inspection did not persist presentation deselection");
            undoGraphAction();
            if (selectedPresentationNodeId !== "discovery.domain.research") throw new Error("edge-inspection deselection was not undoable");
            redoGraphAction();
            researchShell = document.querySelector('[data-presentation-node-id="discovery.domain.research"]');
            researchShell.dispatchEvent(new MouseEvent("click", {bubbles: true}));
            setNodeSelection(["root"], "root", "explicit");
            if (selectedPresentationNodeId !== null || !selectedNodeIds.has("root")) throw new Error("canonical selection did not clear presentation selection");
            researchShell = document.querySelector('[data-presentation-node-id="discovery.domain.research"]');
            researchShell.dispatchEvent(new MouseEvent("click", {bubbles: true}));
            researchShell.dispatchEvent(new MouseEvent("dblclick", {bubbles: true, altKey: true}));
            if (!collapsedPresentationNodes.has("discovery.domain.research") || !nodeElement("root")) throw new Error("presentation collapse hid members or failed to collapse self");
            researchShell = document.querySelector('[data-presentation-node-id="discovery.domain.research"]');
            researchShell.dispatchEvent(new MouseEvent("dblclick", {bubbles: true, altKey: true}));
            if (collapsedPresentationNodes.has("discovery.domain.research")) throw new Error("presentation node did not restore from collapse");
            const rootBeforePresentationDrag = {...lastNodePositions.get("root")};
            researchShell = document.querySelector('[data-presentation-node-id="discovery.domain.research"]');
            const domainDragZoom = zoomLevel;
            researchShell.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0, clientX: 100, clientY: 100}));
            document.dispatchEvent(new MouseEvent("mousemove", {bubbles: true, clientX: 132, clientY: 116}));
            document.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, clientX: 132, clientY: 116}));
            await waitForLayout();
            const expectedPresentationDx = 32 / domainDragZoom;
            if (!presentationNodeOffsets.has("discovery.domain.research") || Math.abs((lastNodePositions.get("root").x - rootBeforePresentationDrag.x) - expectedPresentationDx) > 0.01) throw new Error("presentation drag did not move its shared member exactly once");
            presentationNodeOffsets.clear();
            await updateVisibilityFull({preserveManualPositions: true});
            researchShell = document.querySelector('[data-presentation-node-id="discovery.domain.research"]');
            researchShell.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            if (!hiddenPresentationNodes.has("discovery.domain.research") || !nodeElement("root")) throw new Error("presentation hide changed member visibility");
            for (let cycle = 0; cycle < 3; cycle += 1) {
              togglePresentationNodeHidden("discovery.domain.research");
              togglePresentationNodeHidden("discovery.domain.research");
            }
            togglePresentationNodeHidden("discovery.domain.research");
            if (hiddenPresentationNodes.has("discovery.domain.research")) throw new Error("repeated presentation hide and restore did not settle visible");
            const originalDefaultFacet = presentationNodeControls[0].default_facet;
            presentationNodeControls[0].default_facet = "discovery.domain";
            clearPresentationNodesState();
            if (activePresentationFacets.get("skill-grouping") !== "discovery.domain") throw new Error("Reset discarded JSON presentation defaults");
            presentationNodeControls[0].default_facet = originalDefaultFacet;
            clearPresentationNodesState();
            presentationFacet.value = "discovery.domain";
            presentationFacet.dispatchEvent(new Event("change", {bubbles: true}));
            await waitForLayout();
            const groupingComputeLayout = computeLayout;
            const historyBeforeGroupingFailure = graphUndoStack.length;
            computeLayout = async () => { throw new Error("injected grouping failure"); };
            presentationFacet.value = "discovery.activated_by";
            presentationFacet.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(40);
            if (presentationFacet.value !== "discovery.domain" || !document.querySelector('[data-presentation-node-id="discovery.domain.research"]')) throw new Error("failed grouping layout did not roll back controls and shells");
            const persistedAfterGroupingFailure = JSON.parse(localStorage.getItem(viewerStateKey));
            if (persistedAfterGroupingFailure.presentationNodes.activeFacets["skill-grouping"] !== "discovery.domain" || graphUndoStack.length !== historyBeforeGroupingFailure) throw new Error("failed grouping layout polluted persistence or history");
            computeLayout = groupingComputeLayout;
            const domainShellToggle = document.querySelector('[data-presentation-node="discovery.domain.research"] input');
            domainShellToggle.checked = false;
            domainShellToggle.dispatchEvent(new Event("change", {bubbles: true}));
            if (document.querySelector('[data-presentation-node-id="discovery.domain.research"]') || document.querySelectorAll(".graph-node").length !== canonicalNodeCount || hiddenNodes.size !== 0) throw new Error("hiding a presentation node changed member visibility");
            computeLayout = async () => { throw new Error("injected failure after shell hide"); };
            presentationFacet.value = "discovery.visibility";
            presentationFacet.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(40);
            if (presentationFacet.value !== "discovery.domain" || document.querySelector('[data-presentation-node-id="discovery.domain.research"]')) throw new Error("failed layout lost committed presentation visibility");
            computeLayout = groupingComputeLayout;
            restorePresentationNodesState(null);
            refreshPresentationNodesControls();
            if (presentationFacet.value !== "") throw new Error("legacy viewer state did not restore grouping off");
            await updateVisibilityFull();
            if (manualPositions.get("root")?.x !== retainedManualRootX || !nodeElement("root").hasAttribute("transform")) throw new Error(`turning grouping off did not restore retained manual positions: retained=${retainedManualRootX} current=${manualPositions.get("root")?.x} transform=${nodeElement("root").getAttribute("transform")}`);
            manualPositions.clear();
            await updateVisibilityFull();
            const movedRoot = {...lastNodePositions.get("root"), x: lastNodePositions.get("root").x + 23, y: lastNodePositions.get("root").y + 11};
            manualPositions.set("root", movedRoot);
            hideNodes(["root"]);
            if (!hiddenNodes.has("root") || nodeElement("root")?.style.display !== "none") throw new Error("restore regression setup did not hide root");
            presentationFacet.value = "discovery.domain";
            presentationFacet.dispatchEvent(new Event("change", {bubbles: true}));
            await waitForLayout();
            if (nodeElement("root")) throw new Error("grouping relayout retained explicitly hidden root");
            presentationFacet.value = "";
            presentationFacet.dispatchEvent(new Event("change", {bubbles: true}));
            await waitForLayout();
            showNodes(["root"]);
            await waitForLayout();
            if (!nodeElement("root") || isHiddenNode("root")) throw new Error("restore after grouped relayout did not render root");
            if (manualPositions.get("root")?.x !== movedRoot.x || manualPositions.get("root")?.y !== movedRoot.y) throw new Error("restore after grouped relayout discarded root manual position");
            manualPositions.clear();
            await updateVisibilityFull();
            alpha = nodeElement("alpha"); beta = nodeElement("beta"); root = nodeElement("root");
            if (!(lastNodePositions.get("zeta").x < lastNodePositions.get("alpha").x)) throw new Error("post-layout containment packing reversed dependency direction");
            const fullDetailPositions = ["alpha", "beta", 'odd"node', "zeta"].map(nodeId => lastNodePositions.get(nodeId));
            const fullDetailColumns = new Set(fullDetailPositions.map(position => Math.round(position.x / 10)));
            const fullDetailRows = new Set(fullDetailPositions.map(position => Math.round(position.y / 10)));
            if (fullDetailColumns.size < 2 || fullDetailRows.size < 2) throw new Error("full-detail layout collapsed into a single row or column");
            const nestedPosition = lastNodePositions.get("nested");
            const nestedChildArea = ["alpha", "beta", 'odd"node', "zeta"]
              .map(nodeId => lastNodePositions.get(nodeId))
              .reduce((area, position) => area + position.width * position.height, 0);
            if (nestedPosition.width * nestedPosition.height > nestedChildArea * 4) throw new Error(`container retained excessive empty layout area: container=${nestedPosition.width * nestedPosition.height} children=${nestedChildArea}`);
            const originalSvgWidth = svgEl.getAttribute("width");
            svgEl.setAttribute("width", "100000");
            fitGraph();
            if (zoomLevel <= MIN_ZOOM + 0.001) throw new Error("fit used empty SVG extent instead of visible content bounds");
            svgEl.setAttribute("width", originalSvgWidth);
            setNodeSelection(["alpha"], "alpha", "explicit");
            panX = -8000; panY = -8000; zoomLevel = 0.2; applyTransform();
            document.getElementById("zoom-in-btn").click();
            const alphaFocus = getEffectivePos("alpha");
            const canvasFocusRect = canvasWrapEl.getBoundingClientRect();
            const alphaScreenX = panX + (alphaFocus.x + alphaFocus.width / 2) * zoomLevel;
            const alphaScreenY = panY + (alphaFocus.y + alphaFocus.height / 2) * zoomLevel;
            if (Math.abs(alphaScreenX - canvasFocusRect.width / 2) > 2 || Math.abs(alphaScreenY - canvasFocusRect.height / 2) > 2) throw new Error("zoom control targeted empty canvas instead of selected node");
            deselect();
            fitGraph();
            const makeTouch = (identifier, x, y) => new Touch({identifier, target: canvasWrapEl, clientX: x, clientY: y});
            const dispatchTouches = (type, touches, changedTouches = touches) => canvasWrapEl.dispatchEvent(new TouchEvent(type, {touches, targetTouches: touches, changedTouches, bubbles: true, cancelable: true}));
            const zoomBeforePinch = zoomLevel;
            const pinchStart = [makeTouch(1, 400, 400), makeTouch(2, 600, 400)];
            dispatchTouches("touchstart", pinchStart);
            const pinchMove = [makeTouch(1, 350, 400), makeTouch(2, 650, 400)];
            dispatchTouches("touchmove", pinchMove);
            dispatchTouches("touchend", [], pinchMove);
            if (!(zoomLevel > zoomBeforePinch)) throw new Error("pinch gesture did not zoom around its midpoint");
            const zoomBeforeTwoFingerTap = zoomLevel;
            const twoFingerTap = [makeTouch(3, 440, 420), makeTouch(4, 560, 420)];
            dispatchTouches("touchstart", twoFingerTap);
            dispatchTouches("touchend", [], twoFingerTap);
            if (!(zoomLevel < zoomBeforeTwoFingerTap)) throw new Error("two-finger tap did not zoom out");
            const zoomBeforeDoubleTap = zoomLevel;
            const singleTap = [makeTouch(5, 500, 450)];
            dispatchTouches("touchstart", singleTap); dispatchTouches("touchend", [], singleTap);
            dispatchTouches("touchstart", singleTap); dispatchTouches("touchend", [], singleTap);
            if (!(zoomLevel > zoomBeforeDoubleTap)) throw new Error("one-finger double-tap did not zoom in");
            fitGraph();
            if (!alpha.hasAttribute("tabindex")) throw new Error("nodes are not keyboard focusable");
            if (!document.querySelector(".legend-row[tabindex]")) throw new Error("legend is not keyboard focusable");
            if (!document.querySelector('meta[name="viewport"]')) throw new Error("mobile viewport metadata missing");
            if (!document.getElementById("details").closest("#left-panel")) throw new Error("selection details are not in left inspector");
            const expectedInitialPanelState = window.innerWidth <= 720 ? "false" : "true";
            if (document.getElementById("left-panel-toggle").getAttribute("aria-expanded") !== expectedInitialPanelState) throw new Error("left inspector initial responsive state was wrong");
            if (document.getElementById("panel-toggle").getAttribute("aria-expanded") !== expectedInitialPanelState) throw new Error("right controls initial responsive state was wrong");
            const cheatsheetSection = document.querySelector('[data-section-id="cheatsheet"]');
            const cheatsheetDetails = document.getElementById("cheatsheet-details");
            if (cheatsheetSection.parentElement !== panelContent || cheatsheetSection.previousElementSibling !== document.getElementById("panel-title")) throw new Error("How to use did not remain immediately below the graph title");
            if (cheatsheetSection.querySelector(".drag-handle")) throw new Error("fixed How to use section retained a reorder handle");
            if (document.getElementById("advanced-controls-slot").contains(cheatsheetSection)) throw new Error("How to use was moved into Advanced controls");
            window.localStorage.setItem(viewerStateKey + "::sidebar", JSON.stringify(["advanced", "cheatsheet", "filters", "unknown", "cheatsheet"]));
            restoreSidebarOrder();
            if (cheatsheetSection.previousElementSibling !== document.getElementById("panel-title")) throw new Error("legacy persisted order displaced fixed How to use section");
            cheatsheetDetails.open = true;
            if (!cheatsheetDetails.textContent.includes("Metadata-driven changes are explained under Edge presentation")) throw new Error("How to use does not point to metadata presentation explanations");
            document.getElementById("panel-toggle").click();
            document.getElementById("panel-toggle").click();
            if (!cheatsheetDetails.open || cheatsheetSection.previousElementSibling !== document.getElementById("panel-title")) throw new Error("right-panel collapse changed How to use state or position");
            const bundledEdges = document.querySelectorAll('.edge-path[data-source-node-id="alpha"][data-target-node-id="beta"]');
            if (bundledEdges.length !== 1 || bundledEdges[0].dataset.bundle !== "true") throw new Error("parallel relationships were not bundled into one path");
            if (getComputedStyle(bundledEdges[0]).strokeDasharray !== "none" || !bundledEdges[0].getAttribute("stroke").startsWith("url(") || getComputedStyle(bundledEdges[0]).strokeWidth !== "4.25px" || !bundledEdges[0].style.filter.includes("edge-presentation-filter")) throw new Error("mixed-relation edges did not use the configured gradient and outline presentation");
                const mixedGradientId = bundledEdges[0].getAttribute("stroke").match(/#([^)]+)/)?.[1];
            const mixedGradientColors = Array.from(document.getElementById(mixedGradientId)?.querySelectorAll("stop") || []).map(stop => stop.getAttribute("stop-color"));
            if (!mixedGradientColors.includes("#2563eb") || !mixedGradientColors.includes("#b45309")) throw new Error("mixed-relation gradient omitted constituent relation colors");
            const mixedPresentation = document.querySelector('.legend-row[data-legend-kind="edge-presentation"][data-type="mixed_type_bundle"]');
            if (!mixedPresentation || !mixedPresentation.textContent.includes("Mixed edge evidence") || !mixedPresentation.getAttribute("aria-label").includes("Combines several semantic relation types")) throw new Error("mixed-relation metadata legend entry missing or incomplete");
                const mixedPresentationLine = mixedPresentation.querySelector(".legend-icon path:not(.edge-presentation-outline)");
            if (!mixedPresentationLine.getAttribute("stroke").startsWith("url(") || getComputedStyle(mixedPresentationLine).strokeWidth !== getComputedStyle(bundledEdges[0]).strokeWidth || !mixedPresentation.querySelector(".edge-presentation-outline")) throw new Error("metadata legend sample diverged from rendered mixed edges");
            const confidenceEdge = document.querySelector('.edge-path[data-source-node-id="zeta"][data-target-node-id="alpha"]');
            const dependencyLegend = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="dependency"] .legend-icon path');
            if (getComputedStyle(confidenceEdge).strokeDasharray !== getComputedStyle(dependencyLegend).strokeDasharray) throw new Error("confidence overrode the relationship type dash pattern");
            const sameTypePresentation = document.querySelector('.legend-row[data-legend-kind="edge-presentation"][data-type="same_type_bundle"]');
            if (confidenceEdge.dataset.bundle !== "true" || !sameTypePresentation) throw new Error("same-type bundle metadata presentation missing");
            if (getComputedStyle(sameTypePresentation.querySelector(".legend-icon path")).strokeWidth !== getComputedStyle(confidenceEdge).strokeWidth) throw new Error("same-type bundle legend width diverged from rendered edge");
            const aggregatePresentation = document.querySelector('.legend-row[data-legend-kind="edge-presentation"][data-type="aggregate"]');
            if (!aggregatePresentation || aggregatePresentation.dataset.present !== "false" || !aggregatePresentation.classList.contains("unavailable")) throw new Error("absent aggregate presentation was not retained as a dimmed legend entry");
            if (!aggregatePresentation.getAttribute("aria-label").includes("Not present in the current graph")) throw new Error("unavailable metadata presentation lacks accessible status");
            bundledEdges[0].dispatchEvent(new MouseEvent("click", {bubbles: true}));
            if (!document.getElementById("details").textContent.includes("3 visible relationships") || !document.getElementById("details").textContent.includes("updates records")) throw new Error("bundle inspector lost constituent annotations");
            const containmentEdge = document.querySelector('.edge-path[data-source-node-id="root"][data-target-node-id="alpha"]');
            const rootPos = lastNodePositions.get("root");
            const coordinates = (containmentEdge?.getAttribute("d") || "").match(/-?\\d+(?:\\.\\d+)?/g)?.map(Number) || [];
            for (let index = 0; index + 1 < coordinates.length; index += 2) {
              const x = coordinates[index]; const y = coordinates[index + 1];
              if (x < rootPos.x - 1 || x > rootPos.x + rootPos.width + 1 || y < rootPos.y - 1 || y > rootPos.y + rootPos.height + 1) throw new Error(`containment edge routed outside its container: root=${JSON.stringify(rootPos)} nested=${JSON.stringify(lastNodePositions.get("nested"))} alpha=${JSON.stringify(lastNodePositions.get("alpha"))} path=${coordinates.join(",")}`);
            }
            const lifecycleRenderVersion = renderVersion;
            const initialBundlePath = bundledEdges[0].getAttribute("d");
            const lifecycleParentLegend = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="dependency"]');
            const lifecycleChildLegend = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="uses"]');
            lifecycleParentLegend.click();
            if (bundledEdges[0].style.display !== "none" || arrowForPath(bundledEdges[0])?.style.display !== "none") throw new Error("edge legend did not hide path and arrowhead");
            if (renderVersion !== lifecycleRenderVersion) throw new Error("edge legend hide triggered relayout");
            lifecycleParentLegend.click();
            if (bundledEdges[0].style.display === "none" || arrowForPath(bundledEdges[0])?.style.display === "none") throw new Error("edge legend did not restore path and arrowhead");
            if (bundledEdges[0].getAttribute("d") !== initialBundlePath || renderVersion !== lifecycleRenderVersion) throw new Error("edge legend restore changed layout");
            lifecycleChildLegend.click();
            if (bundledEdges[0].style.display === "none") throw new Error("hiding one bundle constituent hid surviving relationship types");
            bundledEdges[0].dispatchEvent(new MouseEvent("click", {bubbles: true}));
            if (!document.getElementById("details").textContent.includes("1 visible relationship") || document.getElementById("details").textContent.includes("updates records")) throw new Error("bundle inspector ignored constituent visibility");
            lifecycleChildLegend.click();
            bundledEdges[0].dispatchEvent(new MouseEvent("click", {bubbles: true}));
            if (!document.getElementById("details").textContent.includes("3 visible relationships")) throw new Error("bundle constituent restore did not restore inspector data");
            const alphaBeforeDrag = {...getEffectivePos("alpha")};
            alpha.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, button: 0, clientX: 200, clientY: 200}));
            document.dispatchEvent(new MouseEvent("mousemove", {bubbles: true, clientX: 224, clientY: 212}));
            document.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, clientX: 224, clientY: 212}));
            await delay(20);
            const alphaAfterDrag = getEffectivePos("alpha");
            const movedBundlePath = bundledEdges[0].getAttribute("d");
            if (!alphaAfterDrag || (alphaAfterDrag.x === alphaBeforeDrag.x && alphaAfterDrag.y === alphaBeforeDrag.y)) throw new Error("node drag did not update effective position");
            if (movedBundlePath === initialBundlePath || renderVersion !== lifecycleRenderVersion) throw new Error("node drag did not reroute edge in place");
            alpha.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(20);
            if (bundledEdges[0].style.display !== "none") throw new Error("hiding moved node retained incident edge");
            const indirectAfterHide = document.querySelector('.edge-path[data-source-node-id="zeta"][data-target-node-id="beta"][data-edge-type="indirect"]');
            if (!indirectAfterHide || indirectAfterHide.style.display === "none" || !indirectAfterHide.getAttribute("d")) throw new Error("hiding middle node did not project an indirect edge between its visible neighbors");
            const indirectLegend = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="indirect"] .legend-icon path');
            if (getComputedStyle(indirectAfterHide).strokeDasharray !== getComputedStyle(indirectLegend).strokeDasharray) throw new Error("derived edge did not retain its relationship type dash pattern");
            const lifecycleRestore = Array.from(document.querySelectorAll(".hidden-node-item")).find(item => item.textContent.includes("Alpha API"));
            if (!lifecycleRestore) throw new Error("hidden moved node was not restorable");
            lifecycleRestore.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
            await delay(20);
            if (bundledEdges[0].style.display === "none" || bundledEdges[0].getAttribute("d") !== movedBundlePath) throw new Error("restoring moved node did not restore edge geometry");
            if (document.querySelector('.edge-path[data-source-node-id="zeta"][data-target-node-id="beta"][data-edge-type="indirect"]')) throw new Error("restoring middle node retained its obsolete indirect projection");
            const movedContainmentEdge = document.querySelector('.edge-path[data-source-node-id="root"][data-target-node-id="alpha"]');
            const movedCoordinates = (movedContainmentEdge?.getAttribute("d") || "").match(/-?\\d+(?:\\.\\d+)?/g)?.map(Number) || [];
            for (let index = 0; index + 1 < movedCoordinates.length; index += 2) {
              const x = movedCoordinates[index]; const y = movedCoordinates[index + 1];
              if (x < rootPos.x - 1 || x > rootPos.x + rootPos.width + 1 || y < rootPos.y - 1 || y > rootPos.y + rootPos.height + 1) throw new Error("moved containment edge escaped its container");
            }
            alpha.dispatchEvent(new MouseEvent("click", {bubbles: true}));
            await delay(220);
            beta.dispatchEvent(new MouseEvent("click", {bubbles: true, ctrlKey: true}));
            await delay(220);
            if (selectedNodeIds.size !== 2 || selectedNodeId !== "beta" || !alpha.classList.contains("selected") || !beta.classList.contains("selected")) throw new Error("additive node selection failed");
            if (!document.getElementById("details").textContent.includes("2 nodes selected")) throw new Error("multi-selection summary missing");
            const selectionRenderVersion = renderVersion;
            document.getElementById("dim-selected-btn").click();
            if (!alpha.classList.contains("user-dimmed") || !beta.classList.contains("user-dimmed")) throw new Error("bulk dim did not affect selection");
            if (renderVersion !== selectionRenderVersion) throw new Error("bulk dim triggered graph relayout");
            document.getElementById("dim-selected-btn").click();
            document.getElementById("hide-selected-btn").click();
            await delay(20);
            if (!hiddenNodes.has("alpha") || !hiddenNodes.has("beta") || selectedNodeIds.size !== 0) throw new Error("bulk hide did not consume selection");
            for (const label of ["Alpha API", "Beta Source"]) {
              const restoreItem = Array.from(document.querySelectorAll(".hidden-node-item")).find(item => item.textContent.includes(label));
              if (!restoreItem) throw new Error("bulk-hidden node missing from restoration list");
              restoreItem.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
              await delay(20);
            }
            const renderVersionBeforeResize = renderVersion;
            const leftWidthBefore = leftPanelWidth;
            document.getElementById("left-panel-resize").dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
            if (leftPanelWidth <= leftWidthBefore) throw new Error("keyboard resize did not widen left inspector");
            if (renderVersion !== renderVersionBeforeResize) throw new Error("sidebar resize triggered graph relayout");
            document.getElementById("left-panel-toggle").click();
            if (!layoutEl.classList.contains("left-panel-collapsed") || layoutEl.classList.contains("right-panel-collapsed")) throw new Error("left collapse affected wrong panel");
            document.getElementById("left-panel-toggle").click();
            document.getElementById("panel-toggle").click();
            if (!layoutEl.classList.contains("right-panel-collapsed") || layoutEl.classList.contains("left-panel-collapsed")) throw new Error("right collapse affected wrong panel");
            document.getElementById("panel-toggle").click();
            const odd = nodeElement('odd"node');
            odd.dispatchEvent(new MouseEvent("mouseenter", {bubbles: true, clientX: window.innerWidth - 1, clientY: window.innerHeight - 1}));
            const tooltipRect = document.getElementById("tooltip").getBoundingClientRect();
            if (tooltipRect.right > window.innerWidth || tooltipRect.bottom > window.innerHeight) throw new Error("tooltip escaped viewport");
            odd.dispatchEvent(new MouseEvent("mouseleave", {bubbles: true}));
            if (window.innerWidth <= 720 && getComputedStyle(document.querySelector(".layout")).gridTemplateColumns.split(" ").length !== 1) throw new Error("mobile layout kept fixed sidebar column");
            const alphaTransform = alpha.getAttribute("transform");
            const search = document.getElementById("graph-filter-search");
            search.focus(); search.value = "alpha";
            search.dispatchEvent(new Event("input", {bubbles: true}));
            if (!selectedNodeIds.has("alpha") || selectionSource !== "search") throw new Error("find did not create node selection");
            if (beta.classList.contains("selected") || beta.style.display === "none") throw new Error("find changed nonmatching node visibility");
            if (document.querySelector('[data-node-id="alpha"]') !== alpha) throw new Error("filter replaced surviving node");
            if (alpha.getAttribute("transform") !== alphaTransform) throw new Error("filter moved surviving node");
            search.value = "beta"; search.dispatchEvent(new Event("input", {bubbles: true}));
            await delay(20);
            if (selectedNodeIds.has("alpha") || !selectedNodeIds.has("beta")) throw new Error("find selection stayed stale");
            search.value = "uses"; search.dispatchEvent(new Event("input", {bubbles: true}));
            await delay(20);
            if (alpha.style.display === "none" || beta.style.display === "none") throw new Error("matching relation endpoints were not retained");
            if (!selectedNodeIds.has("alpha") || !selectedNodeIds.has("beta")) throw new Error("relation find did not select endpoints");
            document.getElementById("dim-selected-btn").click();
            if (!dimmedNodes.has("alpha") || !dimmedNodes.has("beta")) throw new Error("search selection did not share bulk dim action");
            document.getElementById("dim-selected-btn").click();
            search.value = ""; search.dispatchEvent(new Event("input", {bubbles: true}));
            await delay(20);
            if (selectedNodeIds.size !== 0 || alpha.style.display === "none" || beta.style.display === "none") throw new Error("clearing find left stale selection or visibility");
            search.value = "alpha"; search.dispatchEvent(new Event("input", {bubbles: true}));
            await delay(20);
            const relation = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="dependency"]');
            relation.click();
            await delay(20);
            const relationChild = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="uses"]');
            if (relationChild.getAttribute("aria-disabled") !== "true" || relationChild.getAttribute("aria-pressed") !== "false") throw new Error("edge-category child contradicted excluded parent");
            const edge = document.querySelector('.edge-path[data-source-node-id="alpha"][data-target-node-id="beta"]');
            if (edge && edge.style.display !== "none") throw new Error("relation facet did not hide edge");
            if (edge && !edge.hasAttribute("tabindex")) throw new Error("edges are not keyboard focusable");
            document.getElementById("visibility-undo-btn").click();
            await delay(20);
            if (relation.getAttribute("aria-pressed") !== "true" || (edge && edge.style.display === "none")) throw new Error("visibility undo did not restore relation");
            search.focus(); search.value = "beta";
            search.dispatchEvent(new Event("input", {bubbles: true}));
            search.dispatchEvent(new Event("change", {bubbles: true}));
            if (!selectedNodeIds.has("beta")) throw new Error("committed search did not retain its selection");
            const summary = document.getElementById("filter-summary").textContent;
            if (!summary.includes("nodes") || !summary.includes("relations")) throw new Error("visibility summary missing");
            document.getElementById("filter-clear").click();
            const dataKind = document.querySelector('.legend-row[data-legend-kind="node"][data-legend-facet="kind"][data-type="data"]');
            dataKind.click();
            if (!selectedNodeIds.has("beta")) throw new Error("color legend did not select its nodes");
            document.getElementById("visibility-undo-btn").click();
            if (selectedNodeIds.has("beta")) throw new Error("undo did not restore color selection");
            document.getElementById("visibility-redo-btn").click();
            if (!selectedNodeIds.has("beta")) throw new Error("redo did not restore color selection");
            document.getElementById("filter-clear").click();
            const parentCategory = document.querySelector('.legend-row.legend-parent-row[data-legend-kind="node"][data-type="element"]');
            parentCategory.click();
            if (parentCategory.getAttribute("aria-pressed") !== "true" || selectedNodeIds.size !== docData.entities.length) throw new Error("node-category parent did not select descendants");
            parentCategory.click();
            if (parentCategory.getAttribute("aria-pressed") !== "false" || selectedNodeIds.size !== 0) throw new Error("second node-category click did not clear descendants");
            document.getElementById("reset-btn").dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await waitForLayout();
            if (document.getElementById("graph-filter-search").value !== "") throw new Error("full reset kept search");
            if (presentationFacet.value !== "") throw new Error("full reset kept metadata grouping");
            if (selectedNodeIds.size !== 0) throw new Error("full reset kept node-category selection");
            const legendParent = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="dependency"]');
            const legendChild = document.querySelector('.legend-row[data-legend-kind="edge"][data-type="uses"]');
            legendParent.click();
            if (legendChild.getAttribute("aria-disabled") !== "true") throw new Error("edge legend hierarchy did not disable child");
            legendParent.click();
            if (document.getElementById("focus-toggle")) throw new Error("obsolete global ancestor focus control remains");
            document.getElementById("reset-btn").dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await waitForLayout();
            const restoredOdd = nodeElement('odd"node');
            restoredOdd.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
            await delay(20);
            const hiddenItem = Array.from(document.querySelectorAll(".hidden-node-item")).find(item => item.textContent.includes("Odd Node"));
            if (!hiddenItem || !hiddenItem.closest("#left-panel") || hiddenItem.getAttribute("role") !== "button" || hiddenItem.tabIndex !== 0) throw new Error("hidden node is not keyboard accessible in left inspector");
            hiddenItem.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
            await delay(20);
            if (restoredOdd.style.display === "none") throw new Error("keyboard restore did not reveal hidden node");
            const detailLevel = document.getElementById("graph-detail-level");
            detailLevel.value = "overview";
            detailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(250);
            const overviewRoot = document.querySelector('[data-node-id="root"]');
            const overviewNested = document.querySelector('[data-node-id="nested"]');
            const overviewAlpha = document.querySelector('[data-node-id="alpha"]');
            if (!overviewRoot?.querySelector(".detail-promoted")) throw new Error("overview did not promote container label");
            if (!overviewRoot.querySelector(".detail-promoted-branch") || !overviewNested?.querySelector(".detail-promoted-leaf")) throw new Error("detail promotion did not distinguish branch and leaf containers");
            const rootLabelSize = parseFloat(getComputedStyle(overviewRoot.querySelector(".node-label")).fontSize);
            const nestedLabelSize = parseFloat(getComputedStyle(overviewNested.querySelector(".node-label")).fontSize);
            if (!(rootLabelSize > nestedLabelSize)) throw new Error("nested container label outranked its supernode");
            if (overviewAlpha && overviewAlpha.style.display !== "none") throw new Error("overview retained fine-detail node");
            Object.defineProperty(window, "innerWidth", {value: 650, configurable: true});
            window.dispatchEvent(new Event("resize"));
            if (!layoutEl.classList.contains("narrow-layout")) throw new Error("narrow viewport did not enter responsive layout");
            if (document.getElementById("left-panel-toggle").getAttribute("aria-expanded") !== "false" || document.getElementById("panel-toggle").getAttribute("aria-expanded") !== "false") throw new Error("narrow viewport did not collapse both panels");
            Object.defineProperty(window, "innerWidth", {value: 800, configurable: true});
            window.dispatchEvent(new Event("resize"));
            if (document.getElementById("left-panel-toggle").getAttribute("aria-expanded") !== "true" || document.getElementById("panel-toggle").getAttribute("aria-expanded") !== "true") throw new Error("desktop panel preferences were not restored");
            const transitionDetailLevel = document.getElementById("graph-detail-level");
            transitionDetailLevel.value = "item";
            transitionDetailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(250);
            panX = -100000; panY = -100000; zoomLevel = MIN_ZOOM; hasFittedOnce = true; applyTransform();
            const preTransitionNodeCount = document.querySelectorAll(".graph-node").length;
            const immediateComputeLayout = computeLayout;
            computeLayout = async (...args) => {
              await delay(180);
              return immediateComputeLayout(...args);
            };
            transitionDetailLevel.value = "overview";
            transitionDetailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(20);
            if (preTransitionNodeCount === 0 || document.querySelectorAll(".graph-node").length === 0) throw new Error("detail transition blanked graph before replacement layout was ready");
            await delay(220);
            const visibleAfterTransition = Array.from(document.querySelectorAll(".graph-node")).filter(node => node.style.display !== "none");
            if (!visibleAfterTransition.length || !visibleAfterTransition.every(node => ["root", "nested"].includes(node.dataset.nodeId))) throw new Error("module detail transition did not settle on module nodes");
            const fittedRoot = lastNodePositions.get("root");
            const fittedRootX = panX + (fittedRoot.x + fittedRoot.width / 2) * zoomLevel;
            const fittedRootY = panY + (fittedRoot.y + fittedRoot.height / 2) * zoomLevel;
            const fittedCanvas = canvasWrapEl.getBoundingClientRect();
            if (fittedRootX < 0 || fittedRootX > fittedCanvas.width || fittedRootY < 0 || fittedRootY > fittedCanvas.height) throw new Error("detail relayout retained stale off-canvas viewport");
            computeLayout = immediateComputeLayout;
            let overlappingLayoutCall = 0;
            computeLayout = async (...args) => {
              overlappingLayoutCall += 1;
              await delay([0, 180, 100, 20][overlappingLayoutCall] || 20);
              return immediateComputeLayout(...args);
            };
            transitionDetailLevel.value = "item";
            transitionDetailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            transitionDetailLevel.value = "component";
            transitionDetailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            transitionDetailLevel.value = "overview";
            transitionDetailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(30);
            if (document.querySelectorAll(".graph-node").length === 0) throw new Error("overlapping detail renders blanked the committed graph");
            await delay(220);
            const visibleAfterOverlap = Array.from(document.querySelectorAll(".graph-node")).filter(node => node.style.display !== "none");
            if (!visibleAfterOverlap.length || !visibleAfterOverlap.every(node => ["root", "nested"].includes(node.dataset.nodeId))) throw new Error("stale detail render replaced the latest selection");
            computeLayout = async () => {
              await delay(40);
              throw new Error("injected layout failure");
            };
            transitionDetailLevel.value = "item";
            transitionDetailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(70);
            if (document.querySelectorAll(".graph-node").length === 0) throw new Error("failed layout discarded the committed graph");
            if (!document.getElementById("elk-status").textContent.includes("injected layout failure")) throw new Error("layout failure was not reported");
            computeLayout = immediateComputeLayout;
            transitionDetailLevel.value = "component";
            transitionDetailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(250);
            const componentVisibleIds = new Set(Array.from(document.querySelectorAll(".graph-node")).filter(node => node.style.display !== "none").map(node => node.dataset.nodeId));
            if (!componentVisibleIds.has("root") || !componentVisibleIds.has("nested") || !componentVisibleIds.has("beta") || componentVisibleIds.has("alpha")) throw new Error("renderer did not recover to component detail after layout failure");
            transitionDetailLevel.value = "item";
            transitionDetailLevel.dispatchEvent(new Event("change", {bubbles: true}));
            await delay(250);
            const nestedForHide = nodeElement("nested");
            nestedForHide.dispatchEvent(new MouseEvent("click", {bubbles: true}));
            await delay(220);
            document.getElementById("hide-selected-btn").click();
            await delay(20);
            for (const nodeId of ["nested", "alpha", "beta", 'odd"node']) {
              const hiddenDescendant = nodeElement(nodeId);
              if (hiddenDescendant && hiddenDescendant.style.display !== "none") throw new Error("hiding container retained descendant " + nodeId);
            }
            const hiddenNestedItem = Array.from(document.querySelectorAll(".hidden-node-item")).find(item => item.textContent.includes("Nested"));
            if (!hiddenNestedItem) throw new Error("hidden container was not restorable");
            hiddenNestedItem.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
            await delay(20);
            for (const nodeId of ["nested", "alpha", "beta", 'odd"node']) {
              const restoredDescendant = nodeElement(nodeId);
              if (!restoredDescendant || restoredDescendant.style.display === "none") throw new Error("restoring container did not restore descendant " + nodeId);
            }
            const restoredBundle = document.querySelector('.edge-path[data-source-node-id="alpha"][data-target-node-id="beta"]');
            if (!restoredBundle || restoredBundle.style.display === "none" || !restoredBundle.getAttribute("d")) throw new Error("restoring container did not restore descendant edge geometry");
            nodeElement("nested").dispatchEvent(new MouseEvent("dblclick", {bubbles: true, altKey: true}));
            await delay(250);
            if (document.querySelectorAll(".graph-node").length === 0 || (nodeElement("alpha") && nodeElement("alpha").style.display !== "none")) throw new Error("container collapse produced an invalid projection");
            nodeElement("nested").dispatchEvent(new MouseEvent("dblclick", {bubbles: true, altKey: true}));
            await delay(250);
            if (!nodeElement("alpha") || nodeElement("alpha").style.display === "none") throw new Error("container expansion did not restore descendants");
            pass();
          } catch (error) { fail(error.message || String(error)); }
        }, 100));
        </script></body>""",
    )
    path = Path("/tmp/officina-filter-smoke.html")
    path.write_text(html, encoding="utf-8")
    if os.environ.get("BUILD_ONLY") == "1":
        return
    with tempfile.TemporaryDirectory() as profile:
        result = subprocess.run(
            [
                chrome,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-crash-reporter",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=7000",
                "--window-size=1440,900",
                "--dump-dom",
                path.as_uri(),
            ],
            check=True, capture_output=True, text=True,
        )
    status_marker = 'data-test-status="'
    status_start = result.stdout.find(status_marker)
    status = (
        result.stdout[status_start + len(status_marker):].split('"', 1)[0]
        if status_start >= 0
        else "missing"
    )
    assert status == "PASS", status

    mobile_html = base_html.replace(
        "</body>",
        """<script>
        window.addEventListener("load", () => setTimeout(() => {
          try {
            const selector = document.getElementById("presentation-node-control-dimension");
            if (!selector || selector.value !== "") throw new Error("mobile presentation control missing or active by default");
            if (!document.querySelector('.presentation-node-control-panel')) throw new Error("mobile presentation panel missing");
            if (!document.querySelector('.layout').classList.contains('narrow-layout')) throw new Error("mobile viewport did not use narrow layout");
            if (document.documentElement.scrollWidth > document.documentElement.clientWidth) throw new Error("mobile page has horizontal overflow");
            document.body.dataset.testStatus = "PASS";
            document.title = "PASS";
          } catch (error) {
            document.body.dataset.testStatus = "FAIL:" + (error.message || String(error));
            document.title = document.body.dataset.testStatus;
          }
        }, 250));
        </script></body>""",
    )
    mobile_path = Path("/tmp/officina-filter-mobile-smoke.html")
    mobile_path.write_text(mobile_html, encoding="utf-8")
    with tempfile.TemporaryDirectory() as profile:
        mobile_result = subprocess.run(
            [
                chrome,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-crash-reporter",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=3000",
                "--window-size=390,844",
                "--dump-dom",
                mobile_path.as_uri(),
            ],
            check=True, capture_output=True, text=True,
        )
    mobile_start = mobile_result.stdout.find(status_marker)
    mobile_status = (
        mobile_result.stdout[mobile_start + len(status_marker):].split('"', 1)[0]
        if mobile_start >= 0
        else "missing"
    )
    assert mobile_status == "PASS", mobile_status
