    // Manual node positions (overrides ELK layout after drag)
    const manualPositions = new Map();
    // ELK-computed positions from last full render
    let lastNodePositions = new Map();
    // Track whether full layout has run at least once
    let hasFullLayout = false;
    // Container index for drag grouping and container node rendering.
    let containerIndex = new Map();

    // Drag state for nodes
    let draggingNodeId = null;
    let draggingNodeIds = [];
    let draggingNodeOffsets = new Map();
    let nodeDragMoved = false;
    let nodeClickTimer = null;
    let dragStartClientX = 0;
    let dragStartClientY = 0;
    const DRAG_THRESHOLD = 5;

    // Pan/zoom state
    let panX = 0, panY = 0, zoomLevel = 1;
    let isPanning = false;
    let panStartClientX = 0, panStartClientY = 0, panStartX = 0, panStartY = 0;
    let hasFittedOnce = false;
    const MIN_ZOOM = 0.08, MAX_ZOOM = 5;

    let renderVersion = 0;
    let elk = null;
    const viewerStateIdentity = docData.graph_id || docData.document?.source_file || docData.document?.title || "document";
    const viewerStateKey = `visualization-v3::${docData.graph_kind || "graph"}::${viewerStateIdentity}`;

    function startBuildRefreshWatcher() {
      if (!/^https?:$/.test(window.location.protocol)) return;
      const matchBuildId = text => {
        const match = text.match(/const GRAPH_BUILD_ID = "([^"]+)"/);
        return match ? match[1] : null;
      };
      const checkForNewBuild = async () => {
        try {
          const url = new URL(window.location.href);
          url.searchParams.set("graph_probe", String(Date.now()));
          const response = await fetch(url.toString(), { cache: "no-store" });
          if (!response.ok) return;
          const nextBuildId = matchBuildId(await response.text());
          if (!nextBuildId || nextBuildId === GRAPH_BUILD_ID) return;
          const reloadUrl = new URL(window.location.href);
          reloadUrl.searchParams.delete("graph_probe");
          reloadUrl.searchParams.set("graph_v", nextBuildId);
          window.location.replace(reloadUrl.toString());
        } catch (error) {}
      };
      window.setInterval(checkForNewBuild, 1500);
    }

    const routingPresets = {
      compact: { extraClearance: 0, parallelSpacing: 4, mergeLaneDistance: 18, nodeSpacing: 12, layerSpacing: 45, edgeNodeSpacing: 8 },
      balanced: { extraClearance: 3, parallelSpacing: 12, mergeLaneDistance: 34, nodeSpacing: 46, layerSpacing: 150, edgeNodeSpacing: 40 },
      spacious: { extraClearance: 16, parallelSpacing: 36, mergeLaneDistance: 80, nodeSpacing: 120, layerSpacing: 210, edgeNodeSpacing: 110 }
    };
    const shapePresets = {
      sharp: { cornerRadius: 0 },
      soft: { cornerRadius: 18 },
      curvy: { cornerRadius: 60 }
    };
    const routingConfig = {
      compactnessPreset: "balanced",
      shapePreset: "soft",
      ...routingPresets.balanced,
      ...shapePresets.soft
    };

    edgeData.forEach(edge => {
      if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
      outgoing.get(edge.source).push(edge);
      if (!incoming.has(edge.target)) incoming.set(edge.target, []);
      incoming.get(edge.target).push(edge);
    });

    function syncRoutingControls() {
      routingCompactnessSelect.value = routingConfig.compactnessPreset;
      routingShapeSelect.value = routingConfig.shapePreset;
      Object.entries(routingInputs).forEach(([key, input]) => {
        input.value = routingConfig[key];
        routingValueEls[key].textContent = routingConfig[key];
      });
    }

    function applyRoutingPatch(patch) {
      Object.assign(routingConfig, patch);
      syncRoutingControls();
    }

    // ── ELK layout ───────────────────────────────────────────────────────────

    function ensureElk() {
      if (elk) return elk;
      if (typeof ELK === "undefined") return null;
      elk = new ELK({workerUrl: ELK_WORKER_URL});
      return elk;
    }

    async function computeLayout(visibleEntities, visibleEdges) {
      const elkInstance = ensureElk();
      if (!elkInstance) throw new Error("ELK failed to load.");
      const graph = {
        id: "root",
        layoutOptions: {
          "elk.algorithm": "layered",
          "elk.direction": "RIGHT",
          "elk.edgeRouting": "ORTHOGONAL",
          "elk.hierarchyHandling": "INCLUDE_CHILDREN",
          "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
          "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
          "elk.separateConnectedComponents": "true",
          "elk.spacing.nodeNode": String(routingConfig.nodeSpacing),
          "elk.layered.spacing.nodeNode": String(routingConfig.nodeSpacing),
          "elk.layered.spacing.nodeNodeBetweenLayers": String(routingConfig.layerSpacing),
          "elk.layered.spacing.edgeNodeBetweenLayers": String(routingConfig.edgeNodeSpacing),
          "elk.padding": "[left=40,top=40,right=40,bottom=40]"
        },
        children: buildContainmentGraph(visibleEntities),
        edges: visibleEdges.map((edge, idx) => ({ id: `elk_edge_${idx}`, sources: [edge.source], targets: [edge.target] }))
      };
        const layoutPromise = elkInstance.layout(graph);
        const layoutTimeout = new Promise(function(_, reject) {
          window.setTimeout(function() {
            reject(new Error("Timed out waiting for ELK layout."));
          }, 15000);
        });
        return Promise.race([layoutPromise, layoutTimeout]);
    }

    function pointsForSection(section) {
      return [section.startPoint, ...(section.bendPoints || []), section.endPoint].filter(Boolean);
    }

    function offsetEndpointAwayFromNode(points, endpointIndex, nodeId) {
      if (points.length === 0) return points;
      const nodePos = lastNodePositions.get(nodeId);
      if (!nodePos) return points;
      const updated = points.map(p => ({ x: p.x, y: p.y }));
      const point = updated[endpointIndex];
      const cx = nodePos.x + nodePos.width / 2;
      const cy = nodePos.y + nodePos.height / 2;
      const dx = point.x - cx;
      const dy = point.y - cy;
      const length = Math.hypot(dx, dy) || 1;
      updated[endpointIndex] = {
        x: point.x + edgeNodeGap() * dx / length,
        y: point.y + edgeNodeGap() * dy / length
      };
      return updated;
    }

    function offsetEdgeEndpoints(points, sourceId, targetId) {
      let updated = offsetEndpointAwayFromNode(points, 0, sourceId);
      updated = offsetEndpointAwayFromNode(updated, updated.length - 1, targetId);
      return updated;
    }

    function enforceVerticalNodeSpacing(children) {
      const flattened = [];
      flattenLayoutNodes(children, 0, 0, flattened);
      const layers = new Map();
      (flattened || []).forEach(node => {
        const key = String(Math.round((node.x || 0) / 20) * 20);
        if (!layers.has(key)) layers.set(key, []);
        layers.get(key).push(node);
      });
      layers.forEach(layer => {
        layer.sort((a, b) => (a.y || 0) - (b.y || 0));
        let nextY = null;
        layer.forEach(node => {
          if (nextY !== null && (node.y || 0) < nextY) node.y = nextY;
          nextY = (node.y || 0) + (node.height || 68) + routingConfig.nodeSpacing;
        });
      });
    }

    function roundedPathForPoints(points, radius = routingConfig.cornerRadius) {
      if (!points.length) return "";
      if (points.length < 3) {
        let d = `M ${points[0].x} ${points[0].y}`;
        for (let i = 1; i < points.length; i++) d += ` L ${points[i].x} ${points[i].y}`;
        return d;
      }
      let d = `M ${points[0].x} ${points[0].y}`;
      for (let i = 1; i < points.length - 1; i++) {
        const prev = points[i - 1], curr = points[i], next = points[i + 1];
        const inDx = curr.x - prev.x, inDy = curr.y - prev.y;
        const outDx = next.x - curr.x, outDy = next.y - curr.y;
        const inLen = Math.hypot(inDx, inDy), outLen = Math.hypot(outDx, outDy);
        if (inLen < 1e-6 || outLen < 1e-6) { d += ` L ${curr.x} ${curr.y}`; continue; }
        const r = Math.min(radius, inLen / 2, outLen / 2);
        const p1x = curr.x - (inDx / inLen) * r, p1y = curr.y - (inDy / inLen) * r;
        const p2x = curr.x + (outDx / outLen) * r, p2y = curr.y + (outDy / outLen) * r;
        d += ` L ${p1x} ${p1y} Q ${curr.x} ${curr.y} ${p2x} ${p2y}`;
      }
      const last = points[points.length - 1];
      d += ` L ${last.x} ${last.y}`;
      return d;
    }

    function pathPointsForArrow(pathEl) {
      try {
        const length = pathEl.getTotalLength();
        if (length <= 0) return null;
        const tip = pathEl.getPointAtLength(length);
        const tail = pathEl.getPointAtLength(Math.max(0, length - 14));
        return { tip, tail };
      } catch (error) {
        return null;
      }
    }

    function arrowForPath(pathEl) {
      if (!pathEl || !pathEl.dataset.edgeId) return null;
      return edgeLayer.querySelector(`.edge-arrow[data-edge-id="${selectorValue(pathEl.dataset.edgeId)}"]`);
    }

    function isHiddenEdgeType(edge) {
      return edgeCategorySetContains(edge.type, hiddenEdgeTypes) || isEdgeFilteredOut(edge);
    }

    function syncArrowheadForPath(pathEl) {
      const arrowEl = arrowForPath(pathEl);
      if (!arrowEl) return;
      if (pathEl.style.display === "none") {
        arrowEl.style.display = "none";
        return;
      }
      const points = pathPointsForArrow(pathEl);
      if (!points) return;
      const dx = points.tip.x - points.tail.x;
      const dy = points.tip.y - points.tail.y;
      const length = Math.hypot(dx, dy) || 1;
      const ux = dx / length;
      const uy = dy / length;
      const size = 8;
      const halfWidth = 4;
      const baseX = points.tip.x - ux * size;
      const baseY = points.tip.y - uy * size;
      const leftX = baseX + -uy * halfWidth;
      const leftY = baseY + ux * halfWidth;
      const rightX = baseX - -uy * halfWidth;
      const rightY = baseY - ux * halfWidth;
      arrowEl.setAttribute("points", `${points.tip.x},${points.tip.y} ${leftX},${leftY} ${rightX},${rightY}`);
      arrowEl.style.display = pathEl.style.display;
      arrowEl.style.opacity = pathEl.style.opacity;
      arrowEl.style.filter = pathEl.style.filter;
      arrowEl.setAttribute("fill", pathEl.style.stroke || pathEl.getAttribute("stroke") || "#111111");
      arrowEl.dataset.sourceNodeId = pathEl.dataset.sourceNodeId;
      arrowEl.dataset.targetNodeId = pathEl.dataset.targetNodeId;
      arrowEl.dataset.bridge = pathEl.dataset.bridge;
    }

    function attachArrowhead(pathEl) {
      const existing = arrowForPath(pathEl);
      if (existing) existing.remove();
      const arrowEl = createSvgElement("polygon");
      arrowEl.setAttribute("class", "edge-arrow");
      arrowEl.dataset.edgeId = pathEl.dataset.edgeId;
      edgeLayer.appendChild(arrowEl);
      syncArrowheadForPath(pathEl);
      return arrowEl;
    }

    function mergedTargetPoints(edge, points, targetCounts) {
      if ((targetCounts.get(edge.target) || 0) <= 1) return points;
      if (points.length < 2) return points;
      const targetPos = lastNodePositions.get(edge.target);
      if (!targetPos) return points;
      const mergedEnd = { x: targetPos.x, y: targetPos.y + targetPos.height / 2 };
      const mergedEntry = { x: targetPos.x - edgeNodeGap() - Math.max(MIN_ARROW_LANDING_RUN, routingConfig.mergeLaneDistance), y: mergedEnd.y };
      const updated = points.map(p => ({ x: p.x, y: p.y }));
      if (updated.length === 2) return [updated[0], mergedEntry, mergedEnd];
      updated[updated.length - 2] = mergedEntry;
      updated[updated.length - 1] = mergedEnd;
      return updated;
    }

    // ── Fast visibility toggle (no ELK re-run) ───────────────────────────────

    function updateVisibilityFast() {
      if (!hasFullLayout) { updateVisibilityFull(); return; }
      renderHiddenNodes();

      // Toggle node elements
      svgEl.querySelectorAll(".graph-node").forEach(nodeEl => {
        const nodeId = nodeEl.dataset.nodeId;
        nodeEl.style.display = isHiddenNode(nodeId) ? "none" : "";
      });

      // Toggle non-bridge edge elements
      edgeLayer.querySelectorAll(".edge-path[data-bridge='false']").forEach(pathEl => {
        const src = pathEl.dataset.sourceNodeId;
        const dst = pathEl.dataset.targetNodeId;
        const sourceHidden = isHiddenNode(src);
        const targetHidden = isHiddenNode(dst);
        const edgeTypeHidden = hiddenEdgeTypes.has(String(pathEl.dataset.edgeType || "unknown"));
        const edgeVisible = (!edgeTypeHidden && !sourceHidden && !targetHidden);
        pathEl.style.display = edgeVisible ? "" : "none";
        syncArrowheadForPath(pathEl);
      });

      // Remove all bridge edges; recompute them from current node positions.
      edgeLayer.querySelectorAll(".edge-path[data-bridge='true']").forEach(el => el.remove());
      edgeLayer.querySelectorAll(".edge-arrow[data-bridge='true']").forEach(el => el.remove());

      const visibleEdges = computeVisibleEdges();
      visibleEdges.forEach(edge => {
        if (!edge.bridge) return;
        const srcPos = getEffectivePos(edge.source);
        const dstPos = getEffectivePos(edge.target);
        if (!srcPos || !dstPos) return;
        const path = createSvgElement("path");
        path.setAttribute("class", "edge-path");
        path.setAttribute("d", manualDoglegPath(srcPos, dstPos));
        const edgeStyle = edgeStyleForType(edge.type);
        const edgeStroke = (edgeStyle && (edgeStyle.stroke || edgeStyle.color)) || edgeColorForTarget(edge.target);
        path.setAttribute("stroke", edgeStroke);
        if (edgeStyle && edgeStyle.dash) path.setAttribute("stroke-dasharray", edgeStyle.dash);
        else path.setAttribute("stroke-dasharray", "6 4");
        path.dataset.edgeId = edge.edge_id || `bridge_${edge.source}_${edge.target}`;
        path.dataset.targetNodeId = edge.target;
        path.dataset.sourceNodeId = edge.source;
        path.dataset.bridge = "true";
        path.dataset.edgeType = String(edge.type || "unknown");
        path.dataset.aggregate = edge.aggregate ? "true" : "false";
        if (edge.aggregate) {
          path.style.strokeWidth = "4";
          path.style.strokeDasharray = "10 4 2 4";
        }
        edgeLayer.appendChild(path);
        attachArrowhead(path);
        bindEdgeHover(path, edge);
      });

      applyAncestorFocus();
    }
