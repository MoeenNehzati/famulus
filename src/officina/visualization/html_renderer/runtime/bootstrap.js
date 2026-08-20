/*
 * Browser runtime for the generic graph viewer.
 *
 * Reading order:
 * 1. Decode the injected graph and restore persistent viewer state.
 * 2. Build containment and edge indexes.
 * 3. Project hidden/collapsed entities into visible nodes and rolled-up edges.
 * 4. Ask ELK for geometry, then paint containers, edges, and ordinary nodes.
 * 5. Bind filtering, focus, drag, zoom, routing, and detail interactions.
 *
 * This file consumes only the generic graph payload. Domain adapters belong in
 * sibling `from_*` packages and must not be recognized here by name.
 */
    const docData = @@OFFICINA_GRAPH_DOCUMENT@@;
    const typeStyleCatalog = new Map(
      (docData.categories || [])
        .filter(category => category && category.id)
        .map(category => [String(category.id), { shape: category.shape, color: category.color }])
    );
    const explicitEdgeStyleCatalog = new Map(
      Object.entries((docData.ui && docData.ui.edge_styles) || {})
        .filter(([edgeType, styles]) => edgeType && styles)
        .map(([edgeType, styles]) => [String(edgeType), {
          color: styles.color || styles.stroke,
          dash: styles.dash,
          stroke: styles.stroke,
        }])
    );
    const defaultEdgeMetadataStyles = {
      aggregate: {
        label: "Hidden detail summary",
        description: "Summarizes lower-level edges hidden by collapse or detail level. A subtle halo marks the summary while the foreground preserves the relationship type's color and dash pattern.",
        style: {stroke_width: 2.8, halo_width: 10, halo_color: "#64748b", halo_opacity: 0.22},
      },
      same_type_bundle: {
        label: "Multiple of same type",
        description: "Combines several edges with the same relationship type. Extra width signals multiplicity while preserving the relationship color and dash pattern.",
        style: {stroke_width: 5},
      },
      mixed_type_bundle: {
        label: "Mixed types",
        description: "Combines edges with different relationship types. The solid gradient uses constituent colors, and a neutral outline keeps the combined edge readable.",
        style: {stroke_width: 5, outline_width: 8, outline_color: "#334155", outline_opacity: 0.32, transition_color: "#f8fafc"},
      },
    };
    const configuredEdgeMetadataStyles = (docData.ui && docData.ui.edge_metadata_styles) || {};
    const edgePresentationFacets = (
      (docData.ui && docData.ui.edge_presentation && docData.ui.edge_presentation.facets) || []
    ).map(facet => ({
      ...facet,
      id: String(facet.id),
      field: String(facet.field),
      variants: (facet.variants || []).map(variant => ({...variant, id: String(variant.id)})),
    }));
    const edgeMetadataStyleCatalog = new Map(
      Object.entries(defaultEdgeMetadataStyles).map(([stateId, defaults]) => {
        const configured = configuredEdgeMetadataStyles[stateId] || {};
        const configuredStyle = configured.style || {};
        const style = {...defaults.style, ...configuredStyle};
        return [stateId, {
          label: configured.label || defaults.label,
          description: configured.description || defaults.description,
          style,
        }];
      })
    );
    const fallbackShapes = @@OFFICINA_CATEGORY_SHAPES@@;
    const fallbackColors = @@OFFICINA_CATEGORY_PALETTE@@;
    const presentCategoryIds = Array.from(
      new Set(
        docData.entities
          .map(entity => String(entity.category || entity.type || ""))
          .filter(value => value && value !== "undefined")
      )
    ).sort();
    const presentNodeTypes = Array.from(
      new Set(docData.entities.map(entity => String(entity.type || "unknown")))
    ).sort();
    function kindComponents(kind) {
      return String(kind || "unknown").split("+").map(value => value.trim()).filter(Boolean);
    }
    const presentNodeKinds = Array.from(new Set(
      docData.entities.flatMap(entity =>
        kindComponents(entity.kind || entity.category || entity.type || "unknown")
      )
    )).sort();
    const shapeByType = new Map(
      presentNodeTypes.map((nodeType, index) => [nodeType, fallbackShapes[index % fallbackShapes.length]])
    );
    const colorByKind = new Map(
      presentNodeKinds.map((nodeKind, index) => [nodeKind, fallbackColors[index % fallbackColors.length]])
    );
    docData.entities.forEach(entity => {
      const components = kindComponents(entity.kind || entity.category || entity.type || "unknown");
      const explicitColor = typeStyleCatalog.get(String(entity.category || entity.type || "unknown"))?.color;
      if (components.length === 1 && explicitColor) colorByKind.set(components[0], explicitColor);
    });
    const representativeByCategory = new Map();
    docData.entities.forEach(entity => {
      const categoryId = String(entity.category || entity.type || "unknown");
      if (!representativeByCategory.has(categoryId)) representativeByCategory.set(categoryId, entity);
    });
    const categoryStyles = {};
    presentCategoryIds.forEach(categoryId => {
      const catalog = typeStyleCatalog.get(categoryId) || {};
      const representative = representativeByCategory.get(categoryId) || {};
      const nodeType = String(representative.type || categoryId);
      const nodeKind = String(representative.kind || representative.category || representative.type || categoryId);
      const componentColors = kindComponents(nodeKind).map(kind => colorByKind.get(kind) || "#566573");
      const fallbackStyle = {
        shape: shapeByType.get(nodeType) || "rect",
        color: componentColors[0] || "#566573",
      };
      const style = {
        shape: catalog.shape || fallbackStyle.shape,
        color: componentColors.length > 1 ? componentColors[0] : (catalog.color || fallbackStyle.color),
        colors: componentColors.length > 1 ? componentColors : [catalog.color || fallbackStyle.color],
        form: catalog.form || representative.presentation?.form || "node",
        tone: catalog.tone || representative.presentation?.tone || "strong",
      };
      categoryStyles[categoryId] = style;
    });
    const typeStyles = categoryStyles;
    const edgePalette = @@OFFICINA_EDGE_PALETTE@@;
    const fallbackEdgeDashes = [null, "9 5", "2 4", "12 4 2 4", "5 3", "1 5"];
    const edgeData = @@OFFICINA_EDGE_DATA@@;
    const edgeById = new Map(edgeData.map(edge => [String(edge.edge_id), edge]));
    const edgePairCounts = new Map();
    edgeData.forEach(edge => {
      const source = String(edge.source);
      const target = String(edge.target);
      const pairKey = source < target ? `${source}::${target}` : `${target}::${source}`;
      edgePairCounts.set(pairKey, (edgePairCounts.get(pairKey) || 0) + 1);
    });
    const hasParallelEdges = Array.from(edgePairCounts.values()).some(count => count > 1);
    const relationSemantics = docData.relation_semantics || {};
    const nodeOmissionRules = relationSemantics.transformations?.node_omission?.rules || [];
    function compileRelationTransitions(rules) {
      const transitions = new Map();
      rules.forEach(rule => {
      (rule.causes || []).forEach(cause => {
        (rule.left_types || []).forEach(leftType => {
          (rule.right_types || []).forEach(rightType => {
            const key = JSON.stringify([String(cause), String(leftType), String(rightType)]);
            transitions.set(key, (rule.outcomes || []).map(outcome => ({
              ruleId: String(rule.id),
              resultType: String(outcome.type),
              fidelity: String(outcome.fidelity),
            })).sort((left, right) =>
              left.resultType.localeCompare(right.resultType) || left.fidelity.localeCompare(right.fidelity)
            ));
          });
        });
      });
      });
      return transitions;
    }
    const relationTransitions = compileRelationTransitions(nodeOmissionRules);
    const projectionResultTypes = nodeOmissionRules.flatMap(rule =>
      (rule.outcomes || []).map(outcome => String(outcome.type))
    );
    const subsumedTypesByType = new Map();
    (relationSemantics.subsumptions || []).forEach(rule => {
      const stronger = String(rule.stronger_type);
      if (!subsumedTypesByType.has(stronger)) subsumedTypesByType.set(stronger, new Set());
      (rule.weaker_types || []).forEach(type => subsumedTypesByType.get(stronger).add(String(type)));
    });
    let dominanceChanged = true;
    while (dominanceChanged) {
      dominanceChanged = false;
      subsumedTypesByType.forEach(weakerTypes => {
        Array.from(weakerTypes).forEach(type => {
          (subsumedTypesByType.get(type) || []).forEach(transitiveType => {
            if (!weakerTypes.has(transitiveType)) {
              weakerTypes.add(transitiveType);
              dominanceChanged = true;
            }
          });
        });
      });
    }
    const presentEdgeTypes = Array.from(
      new Set(
        edgeData.map(edge => String(edge.type || "unknown"))
          .concat(projectionResultTypes)
          .filter(value => value && value !== "undefined")
      )
    ).sort();
    const edgeStyleCatalog = new Map();
    presentEdgeTypes.forEach((edgeType, index) => {
      const explicit = explicitEdgeStyleCatalog.get(edgeType) || {};
      const fallbackStroke = edgePalette[index % edgePalette.length];
      const fallbackDash = fallbackEdgeDashes[index % fallbackEdgeDashes.length];
      edgeStyleCatalog.set(edgeType, {
        stroke: explicit.stroke || explicit.color || fallbackStroke,
        color: explicit.color || explicit.stroke || fallbackStroke,
        dash: Object.prototype.hasOwnProperty.call(explicit, "dash") ? explicit.dash : fallbackDash,
      });
    });
    const renderTypeOverrides = @@OFFICINA_RENDER_TYPE_OVERRIDES@@;

    // DOM refs
    const layoutEl = document.getElementById("layout");
    const panelToggle = document.getElementById("panel-toggle");
    const leftPanelToggle = document.getElementById("left-panel-toggle");
    const leftPanelEl = document.getElementById("left-panel");
    const rightPanelEl = document.getElementById("right-panel");
    const leftPanelResize = document.getElementById("left-panel-resize");
    const rightPanelResize = document.getElementById("right-panel-resize");
    const svgEl = document.getElementById("graph-svg");
    const DEFAULT_NODE_WIDTH = 291;
    const DEFAULT_NODE_HEIGHT = 99;
    const DEFAULT_CONTAINER_WIDTH = 252;
    const DEFAULT_CONTAINER_HEIGHT = 128;
    const MAX_CONTENT_NODE_WIDTH = 416;
    const measuredNodeDimensions = new Map();
    let nodeMeasurementHost = null;

    function containerLayoutMetrics(nodeId) {
      const entity = entityMap.get(String(nodeId));
      const depth = entity
        ? Math.min(nodePresentationState(entity).containmentDepth, 2)
        : 0;
      return [
        {nodeSpacing: 18, layerSpacing: 52, sidePadding: 16, headerPadding: 74, bottomPadding: 16},
        {nodeSpacing: 14, layerSpacing: 36, sidePadding: 10, headerPadding: 58, bottomPadding: 10},
        {nodeSpacing: 10, layerSpacing: 28, sidePadding: 8, headerPadding: 52, bottomPadding: 8},
      ][depth];
    }

    function defaultNodeDimensions(nodeId, {container = false} = {}) {
      const entity = entityMap.get(String(nodeId));
      if (!entity) {
        return {
          width: container ? DEFAULT_CONTAINER_WIDTH : DEFAULT_NODE_WIDTH,
          height: container ? DEFAULT_CONTAINER_HEIGHT : DEFAULT_NODE_HEIGHT,
        };
      }
      const presentationState = nodePresentationState(entity, {forceContainer: container});
      const label = String(entity.label || entity.short_title || entity.id || "");
      const subtitle = String(entity.type + (entity.ref ? " " + entity.ref : ""));
      const cacheKey = JSON.stringify([
        label,
        subtitle,
        presentationState.className,
        presentationState.isContainer,
      ]);
      const cached = measuredNodeDimensions.get(cacheKey);
      if (cached) return {...cached};

      if (!nodeMeasurementHost) {
        nodeMeasurementHost = document.createElement("div");
        nodeMeasurementHost.setAttribute("aria-hidden", "true");
        Object.assign(nodeMeasurementHost.style, {
          position: "absolute",
          visibility: "hidden",
          pointerEvents: "none",
          left: "-10000px",
          top: "0",
          contain: "layout style",
        });
        document.body.appendChild(nodeMeasurementHost);
      }

      const body = document.createElement("div");
      body.className = presentationState.className;
      const compactContainer = presentationState.detailPromoted && !presentationState.hasVisibleChildren;
      const minimumWidth = presentationState.isContainer && !compactContainer
        ? DEFAULT_CONTAINER_WIDTH
        : DEFAULT_NODE_WIDTH;
      const minimumHeight = presentationState.isContainer
        ? (compactContainer ? 88 : DEFAULT_CONTAINER_HEIGHT)
        : DEFAULT_NODE_HEIGHT;
      body.style.width = "max-content";
      body.style.height = "auto";
      body.style.minWidth = `${minimumWidth}px`;
      body.style.maxWidth = `${MAX_CONTENT_NODE_WIDTH}px`;
      body.innerHTML = `<div class="node-label">${escapeHtml(label)}</div><div class="node-subtitle">${escapeHtml(subtitle)}</div>`;
      nodeMeasurementHost.appendChild(body);
      const width = Math.ceil(body.getBoundingClientRect().width);
      const height = Math.ceil(body.scrollHeight);
      body.remove();

      const dimensions = {
        width: Math.max(minimumWidth, width),
        height: Math.max(minimumHeight, height),
      };
      measuredNodeDimensions.set(cacheKey, dimensions);
      return {...dimensions};
    }
    const canvasWrapEl = document.getElementById("canvas-wrap");
    const presentationNodeLayer = document.getElementById("presentation-node-layer");
    const containerLayer = document.getElementById("container-layer");
    const edgeLayer = document.getElementById("edge-layer");
    const nodeLayer = document.getElementById("node-layer");
    const tooltip = document.getElementById("tooltip");
    const details = document.getElementById("details");
    const legend = document.getElementById("legend");
    const hiddenNodesEl = document.getElementById("hidden-nodes");
    const visibilityUndoBtn = document.getElementById("visibility-undo-btn");
    const visibilityRedoBtn = document.getElementById("visibility-redo-btn");
    const hideSelectedBtn = document.getElementById("hide-selected-btn");
    const dimSelectedBtn = document.getElementById("dim-selected-btn");
    const hideUnselectedBtn = document.getElementById("hide-unselected-btn");
    const dimUnselectedBtn = document.getElementById("dim-unselected-btn");
    const elkStatus = document.getElementById("elk-status");
    const rawJsonCodeEl = document.getElementById("raw-json-code");
    const panelContent = document.getElementById("panel-content");
    const routingCompactnessSelect = document.getElementById("routing-compactness");
    const routingGeometrySelect = document.getElementById("routing-geometry");
    const routingParallelRow = document.getElementById("routing-parallel-row");
    const routingGeometryRows = Array.from(document.querySelectorAll("[data-routing-geometry]"));
    const routingInputs = {
      extraClearance: document.getElementById("routing-clearance"),
      cornerRadius: document.getElementById("routing-radius"),
      parallelSpacing: document.getElementById("routing-parallel"),
      mergeLaneDistance: document.getElementById("routing-merge"),
      polylineBend: document.getElementById("routing-polyline-bend"),
      splineTension: document.getElementById("routing-spline-tension"),
      bezierCurvature: document.getElementById("routing-bezier-curvature"),
      nodeSpacing: document.getElementById("routing-node-spacing"),
      layerSpacing: document.getElementById("routing-layer-spacing"),
      edgeNodeSpacing: document.getElementById("routing-edge-node-spacing")
    };
    const routingValueEls = {
      extraClearance: document.getElementById("routing-clearance-value"),
      cornerRadius: document.getElementById("routing-radius-value"),
      parallelSpacing: document.getElementById("routing-parallel-value"),
      mergeLaneDistance: document.getElementById("routing-merge-value"),
      polylineBend: document.getElementById("routing-polyline-bend-value"),
      splineTension: document.getElementById("routing-spline-tension-value"),
      bezierCurvature: document.getElementById("routing-bezier-curvature-value"),
      nodeSpacing: document.getElementById("routing-node-spacing-value"),
      layerSpacing: document.getElementById("routing-layer-spacing-value"),
      edgeNodeSpacing: document.getElementById("routing-edge-node-spacing-value")
    };

    // Core state
    const entityMap = new Map(docData.entities.map(e => [e.id, e]));
    const detailLevels = Array.isArray(docData.detail_levels) ? docData.detail_levels : [];
    const detailLevelRank = new Map(detailLevels.map((level, index) => [String(level.id), index]));
    const defaultDetailLevel = detailLevels.length
      ? String(detailLevels[detailLevels.length - 1].id)
      : null;
    const outgoing = new Map();
    const incoming = new Map();
    const initialVisibility = docData.ui?.visibility || {};
    const hiddenTypes = new Set((initialVisibility.hidden_types || []).map(String));
    const hiddenEdgeTypes = new Set((initialVisibility.hidden_edge_types || []).map(String));
    const hiddenNodes = new Set(
      (initialVisibility.hidden_nodes || []).filter(id => entityMap.has(String(id))).map(String)
    );
    const collapsedContainers = new Set(
      (initialVisibility.collapsed_containers || []).map(String)
    );
    const nodeCategories = new Map(
      docData.entities.map(e => [e.id, String(e.category || e.type || "unknown")])
    );
    const parentByNode = new Map(
      docData.entities
        .filter(entity => typeof entity.container === "string" && entity.container.trim())
        .map(entity => [entity.id, entity.container.trim()])
    );
    const categoryLabels = new Map(
      (docData.categories || [])
        .filter(category => category && category.id)
        .map(category => [String(category.id), category.label || String(category.id)])
    );
    const categoryDescriptions = new Map(
      (docData.categories || [])
        .filter(category => category && category.id)
        .map(category => [String(category.id), category.description || ""])
    );
    const edgeCategoryCatalog = new Map(
      (docData.edge_categories || [])
        .filter(category => category && category.id)
        .map(category => [String(category.id), category])
    );
    const edgeCategoryParent = new Map();
    const edgeCategoryChildren = new Map();
    edgeCategoryCatalog.forEach((category, categoryId) => {
      const parent = String(category.parent || "");
      if (!parent || !edgeCategoryCatalog.has(parent)) return;
      edgeCategoryParent.set(categoryId, parent);
      if (!edgeCategoryChildren.has(parent)) edgeCategoryChildren.set(parent, []);
      edgeCategoryChildren.get(parent).push(categoryId);
    });
    function edgeCategorySetContains(type, values) {
      let current = String(type || "unknown");
      const seen = new Set();
      while (current && !seen.has(current)) {
        if (values.has(current)) return true;
        seen.add(current);
        current = edgeCategoryParent.get(current) || "";
      }
      return false;
    }
    function relevantEdgeCategoryIds() {
      const relevant = new Set(presentEdgeTypes);
      presentEdgeTypes.forEach(type => {
        let parent = edgeCategoryParent.get(type);
        while (parent && !relevant.has(parent)) {
          relevant.add(parent);
          parent = edgeCategoryParent.get(parent);
        }
      });
      return relevant;
    }
    let selectedNodeId = entityMap.has(docData.ui?.focus?.selected_node_id)
      ? docData.ui.focus.selected_node_id
      : null;
    const selectedNodeIds = new Set(selectedNodeId ? [selectedNodeId] : []);
    let selectionSource = "explicit";
    const dimmedNodes = new Set();
    let lastRenderedEdges = [];
    const nodeColorIndex = new Map(
      docData.entities
        .slice()
        .sort((a, b) => {
          const aPos = a.position || 0;
          const bPos = b.position || 0;
          if (aPos !== bPos) return aPos - bPos;
          return a.short_title.localeCompare(b.short_title);
        })
        .map((e, idx) => [e.id, idx])
    );
