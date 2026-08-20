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
    const layoutPreferences = docData.ui?.layout || {};
    const rankDirections = {LR: "RIGHT", RL: "LEFT", TB: "DOWN", BT: "UP"};
    const layoutDirection = rankDirections[layoutPreferences.rankdir] || "RIGHT";
    const preferredAspectRatio = Number(layoutPreferences.aspect_ratio) > 0
      ? Number(layoutPreferences.aspect_ratio)
      : 1.7;
    const routingConfig = {
      compactnessPreset: "balanced",
      geometry: "bezier",
      polylineBend: 50,
      splineTension: 22,
      bezierCurvature: 30,
      ...routingPresets.balanced,
      cornerRadius: 18,
      nodeSpacing: Number.isFinite(layoutPreferences.node_spacing)
        ? layoutPreferences.node_spacing
        : routingPresets.balanced.nodeSpacing,
      layerSpacing: Number.isFinite(layoutPreferences.layer_spacing)
        ? layoutPreferences.layer_spacing
        : routingPresets.balanced.layerSpacing
    };

    edgeData.forEach(edge => {
      if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
      outgoing.get(edge.source).push(edge);
      if (!incoming.has(edge.target)) incoming.set(edge.target, []);
      incoming.get(edge.target).push(edge);
    });

    function syncRoutingControls() {
      routingCompactnessSelect.value = routingConfig.compactnessPreset;
      routingGeometrySelect.value = routingConfig.geometry;
      routingParallelRow.hidden = !hasParallelEdges;
      routingGeometryRows.forEach(row => {
        row.hidden = row.dataset.routingGeometry !== routingConfig.geometry;
      });
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
      const layoutRootId = "__layout_root__";
      const entitiesById = new Map(visibleEntities.map(entity => [entity.id, entity]));
      const visibleIds = new Set(entitiesById.keys());
      const visibleParents = new Map();
      const childrenByParent = new Map([[layoutRootId, []]]);

      visibleEntities.forEach(entity => {
        const declaredParent = typeof entity.container === "string" ? entity.container.trim() : "";
        const parentId = declaredParent && visibleIds.has(declaredParent) ? declaredParent : layoutRootId;
        visibleParents.set(entity.id, parentId);
        const siblings = childrenByParent.get(parentId) || [];
        siblings.push(entity.id);
        childrenByParent.set(parentId, siblings);
      });

      const compareEntityIds = (leftId, rightId) => {
        const left = entitiesById.get(leftId);
        const right = entitiesById.get(rightId);
        const leftPosition = Number.isFinite(left?.position) ? left.position : Number.MAX_SAFE_INTEGER;
        const rightPosition = Number.isFinite(right?.position) ? right.position : Number.MAX_SAFE_INTEGER;
        return leftPosition - rightPosition || leftId.localeCompare(rightId);
      };
      childrenByParent.forEach(children => children.sort(compareEntityIds));

      function directChildOf(parentId, descendantId) {
        if (descendantId === parentId || !visibleIds.has(descendantId)) return null;
        let current = descendantId;
        const seen = new Set();
        while (!seen.has(current)) {
          seen.add(current);
          const currentParent = visibleParents.get(current);
          if (!currentParent) return null;
          if (currentParent === parentId) return current;
          if (currentParent === layoutRootId) return parentId === layoutRootId ? current : null;
          current = currentParent;
        }
        return null;
      }

      function projectedEdges(parentId, childIds) {
        const childSet = new Set(childIds);
        const represented = new Set();
        const projected = [];
        visibleEdges.forEach((edge, index) => {
          const source = directChildOf(parentId, edge.source);
          const target = directChildOf(parentId, edge.target);
          if (!source || !target || source === target || !childSet.has(source) || !childSet.has(target)) return;
          const key = `${source}\u0000${target}`;
          if (represented.has(key)) return;
          represented.add(key);
          projected.push({id: `projected_${index}`, sources: [source], targets: [target]});
        });
        return projected;
      }

      async function runElk(graph) {
        const layoutPromise = elkInstance.layout(graph);
        const layoutTimeout = new Promise(function(_, reject) {
          window.setTimeout(function() {
            reject(new Error("Timed out waiting for ELK layout."));
          }, 15000);
        });
        return Promise.race([layoutPromise, layoutTimeout]);
      }

      async function arrangeChildren(parentId, childNodes, isRoot) {
        const childIds = childNodes.map(child => child.id);
        const edges = projectedEdges(parentId, childIds);
        const requestedAlgorithm = layoutPreferences.elk_algorithm;
        const algorithm = requestedAlgorithm || (edges.length ? "layered" : "rectpacking");
        const rootNodeSpacing = String(routingConfig.nodeSpacing);
        const containerMetrics = isRoot ? null : containerLayoutMetrics(parentId);
        const nodeSpacing = isRoot
          ? routingConfig.nodeSpacing
          : Math.min(routingConfig.nodeSpacing, containerMetrics.nodeSpacing);
        const layerSpacing = isRoot
          ? routingConfig.layerSpacing
          : Math.min(routingConfig.layerSpacing, containerMetrics.layerSpacing);
        const graph = await runElk({
          id: `layout_${parentId}`,
          layoutOptions: {
            "elk.algorithm": algorithm,
            "elk.direction": layoutDirection,
            "elk.edgeRouting": layoutPreferences.edge_routing || "ORTHOGONAL",
            "elk.aspectRatio": String(preferredAspectRatio),
            "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
            "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
            "elk.layered.considerModelOrder.strategy": "PREFER_NODES",
            "elk.layered.considerModelOrder.components": "MODEL_ORDER",
            "elk.separateConnectedComponents": "true",
            "elk.spacing.nodeNode": isRoot ? rootNodeSpacing : String(nodeSpacing),
            "elk.spacing.componentComponent": String(nodeSpacing),
            "elk.layered.spacing.nodeNode": String(nodeSpacing),
            "elk.layered.spacing.nodeNodeBetweenLayers": String(layerSpacing),
            "elk.padding": isRoot
              ? "[left=40,top=40,right=40,bottom=40]"
              : `[left=${containerMetrics.sidePadding},top=${containerMetrics.headerPadding},right=${containerMetrics.sidePadding},bottom=${containerMetrics.bottomPadding}]`
          },
          children: childNodes.map(child => ({id: child.id, width: child.width, height: child.height})),
          edges
        });
        const positions = new Map((graph.children || []).map(child => [child.id, child]));
        childNodes.forEach(child => {
          const position = positions.get(child.id);
          child.x = position?.x || 0;
          child.y = position?.y || 0;
        });
        return {
          width: Math.max(
            isRoot ? 0 : defaultNodeDimensions(parentId, {container: true}).width,
            graph.width || 0,
          ),
          height: Math.max(
            isRoot ? 0 : defaultNodeDimensions(parentId, {container: true}).height,
            graph.height || 0,
          ),
          children: childNodes
        };
      }

      async function layoutEntity(entityId) {
        const childIds = childrenByParent.get(entityId) || [];
        if (!childIds.length) {
          const dimensions = defaultNodeDimensions(entityId);
          return {id: entityId, width: dimensions.width, height: dimensions.height};
        }
        const childNodes = await Promise.all(childIds.map(layoutEntity));
        const arranged = await arrangeChildren(entityId, childNodes, false);
        return {id: entityId, width: arranged.width, height: arranged.height, children: arranged.children};
      }

      const rootIds = childrenByParent.get(layoutRootId) || [];
      const rootNodes = await Promise.all(rootIds.map(layoutEntity));
      const arrangedRoot = await arrangeChildren(layoutRootId, rootNodes, true);
      const absolutePositions = new Map();
      function collect(nodes, offsetX, offsetY) {
        (nodes || []).forEach(node => {
          const absolute = {
            x: offsetX + (node.x || 0),
            y: offsetY + (node.y || 0),
            width: node.width || DEFAULT_NODE_WIDTH,
            height: node.height || DEFAULT_NODE_HEIGHT
          };
          absolutePositions.set(node.id, absolute);
          collect(node.children, absolute.x, absolute.y);
        });
      }
      collect(arrangedRoot.children, 0, 0);
      const edges = visibleEdges.map((edge, index) => {
        const source = absolutePositions.get(edge.source);
        const target = absolutePositions.get(edge.target);
        const startPoint = source
          ? {x: source.x + source.width / 2, y: source.y + source.height / 2}
          : {x: 0, y: 0};
        const endPoint = target
          ? {x: target.x + target.width / 2, y: target.y + target.height / 2}
          : {x: 0, y: 0};
        return {
          id: `elk_edge_${index}`,
          sources: [edge.source],
          targets: [edge.target],
          sections: [{startPoint, endPoint}]
        };
      });
      return {
        id: "root",
        width: arrangedRoot.width,
        height: arrangedRoot.height,
        children: arrangedRoot.children,
        edges
      };
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
          nextY = (node.y || 0) + (node.height || DEFAULT_NODE_HEIGHT) + routingConfig.nodeSpacing;
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
      arrowEl.setAttribute("fill", pathEl.dataset.edgeArrowColor || pathEl.style.stroke || pathEl.getAttribute("stroke") || "#111111");
      if (pathEl.dataset.edgeArrowOpacity) {
        arrowEl.setAttribute("fill-opacity", pathEl.dataset.edgeArrowOpacity);
      } else {
        arrowEl.removeAttribute("fill-opacity");
      }
      arrowEl.dataset.sourceNodeId = pathEl.dataset.sourceNodeId;
      arrowEl.dataset.targetNodeId = pathEl.dataset.targetNodeId;
      arrowEl.dataset.derived = pathEl.dataset.derived;
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
      const missingVisibleNode = docData.entities.some(entity => (
        !isHiddenNode(entity.id) && !nodeElement(entity.id)
      ));
      if (missingVisibleNode) {
        updateVisibilityFull({preserveManualPositions: true});
        return;
      }
      renderHiddenNodes();

      // Toggle node elements
      svgEl.querySelectorAll(".graph-node").forEach(nodeEl => {
        const nodeId = nodeEl.dataset.nodeId;
        nodeEl.style.display = isHiddenNode(nodeId) ? "none" : "";
      });

      // Toggle canonical edge elements without replacing their stable geometry.
      edgeLayer.querySelectorAll(".edge-path[data-derived='false']").forEach(pathEl => {
        const src = pathEl.dataset.sourceNodeId;
        const dst = pathEl.dataset.targetNodeId;
        const sourceHidden = isHiddenNode(src);
        const targetHidden = isHiddenNode(dst);
        const edgeTypeHidden = hiddenEdgeTypes.has(String(pathEl.dataset.edgeType || "unknown"));
        const edgeVisible = (!edgeTypeHidden && !sourceHidden && !targetHidden);
        pathEl.style.display = edgeVisible ? "" : "none";
        syncArrowheadForPath(pathEl);
      });

      // Derived edges depend on the current omission set, so replace only them.
      edgeLayer.querySelectorAll(".edge-path[data-derived='true']").forEach(el => {
        removeEdgePresentationResources(el);
        el.remove();
      });
      edgeLayer.querySelectorAll(".edge-arrow[data-derived='true']").forEach(el => el.remove());

      const visibleEdges = computeVisibleEdges();
      visibleEdges.forEach(edge => {
        if (!edge.derived) return;
        const srcPos = getEffectivePos(edge.source);
        const dstPos = getEffectivePos(edge.target);
        if (!srcPos || !dstPos) return;
        const path = createSvgElement("path");
        path.setAttribute("class", "edge-path");
        path.setAttribute("d", manualDoglegPath(srcPos, dstPos));
        const edgeStyle = edgeStyleForType(edge.type);
        applyEdgeMetadataPresentation(path, edge, edgeStyle, edgeColorForTarget(edge.target));
        path.dataset.edgeId = edge.edge_id || `projection_${edge.source}_${edge.target}`;
        path.dataset.targetNodeId = edge.target;
        path.dataset.sourceNodeId = edge.source;
        path.dataset.derived = "true";
        path.dataset.edgeType = String(edge.type || "unknown");
        path.dataset.aggregate = edge.aggregate ? "true" : "false";
        path.__edgeMeta = edge;
        edgeLayer.appendChild(path);
        syncEdgeMetadataPresentationGeometry(path);
        attachArrowhead(path);
        bindEdgeHover(path, edge);
      });

      syncEdgePresentationLegend();

      refreshEdgeOcclusionMasks();
      applyVisibilityPresentation();
    }
