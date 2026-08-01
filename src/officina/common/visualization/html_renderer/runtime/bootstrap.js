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
    const presentNodeKinds = Array.from(
      new Set(
        docData.entities.map(entity => String(entity.kind || entity.category || entity.type || "unknown"))
      )
    ).sort();
    const shapeByType = new Map(
      presentNodeTypes.map((nodeType, index) => [nodeType, fallbackShapes[index % fallbackShapes.length]])
    );
    const colorByKind = new Map(
      presentNodeKinds.map((nodeKind, index) => [nodeKind, fallbackColors[index % fallbackColors.length]])
    );
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
      const fallbackStyle = {
        shape: shapeByType.get(nodeType) || "rect",
        color: colorByKind.get(nodeKind) || "#566573",
      };
      const style = {
        shape: catalog.shape || fallbackStyle.shape,
        color: catalog.color || fallbackStyle.color,
        form: catalog.form || representative.presentation?.form || "node",
        tone: catalog.tone || representative.presentation?.tone || "strong",
      };
      categoryStyles[categoryId] = style;
    });
    const typeStyles = categoryStyles;
    const edgePalette = @@OFFICINA_EDGE_PALETTE@@;
    const fallbackEdgeDashes = [null, "9 5", "2 4", "12 4 2 4", "5 3", "1 5"];
    const edgeData = @@OFFICINA_EDGE_DATA@@;
    const presentEdgeTypes = Array.from(
      new Set(edgeData.map(edge => String(edge.type || "unknown")).filter(value => value && value !== "undefined"))
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
    const svgEl = document.getElementById("graph-svg");
    const canvasWrapEl = document.getElementById("canvas-wrap");
    const containerLayer = document.getElementById("container-layer");
    const edgeLayer = document.getElementById("edge-layer");
    const nodeLayer = document.getElementById("node-layer");
    const tooltip = document.getElementById("tooltip");
    const details = document.getElementById("details");
    const legend = document.getElementById("legend");
    const removedNodesEl = document.getElementById("removed-nodes");
    const focusToggle = document.getElementById("focus-toggle");
    const deleteNodeBtn = document.getElementById("delete-node-btn");
    const elkStatus = document.getElementById("elk-status");
    const rawJsonCodeEl = document.getElementById("raw-json-code");
    const panelContent = document.getElementById("panel-content");
    const routingCompactnessSelect = document.getElementById("routing-compactness");
    const routingShapeSelect = document.getElementById("routing-shape");
    const routingInputs = {
      extraClearance: document.getElementById("routing-clearance"),
      cornerRadius: document.getElementById("routing-radius"),
      parallelSpacing: document.getElementById("routing-parallel"),
      mergeLaneDistance: document.getElementById("routing-merge"),
      nodeSpacing: document.getElementById("routing-node-spacing"),
      layerSpacing: document.getElementById("routing-layer-spacing"),
      edgeNodeSpacing: document.getElementById("routing-edge-node-spacing")
    };
    const routingValueEls = {
      extraClearance: document.getElementById("routing-clearance-value"),
      cornerRadius: document.getElementById("routing-radius-value"),
      parallelSpacing: document.getElementById("routing-parallel-value"),
      mergeLaneDistance: document.getElementById("routing-merge-value"),
      nodeSpacing: document.getElementById("routing-node-spacing-value"),
      layerSpacing: document.getElementById("routing-layer-spacing-value"),
      edgeNodeSpacing: document.getElementById("routing-edge-node-spacing-value")
    };

    // Core state
    const entityMap = new Map(docData.entities.map(e => [e.id, e]));
    const outgoing = new Map();
    const incoming = new Map();
    const initialVisibility = docData.ui?.visibility || {};
    const hiddenTypes = new Set((initialVisibility.hidden_types || []).map(String));
    const hiddenEdgeTypes = new Set((initialVisibility.hidden_edge_types || []).map(String));
    const hiddenNodes = new Set();
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
    let selectedNodeId = null;
    let focusNodeId = null;
    let ancestorFocusMode = 0; // 0=off, 1=dim non-ancestors, 2=hide non-ancestors
    const ancestorHiddenByFocus = new Set(); // nodes temporarily hidden in mode 2
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

