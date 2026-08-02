    // Generic SVG geometry and edge rerouting.
    function createSvgElement(name) {
      return document.createElementNS("http://www.w3.org/2000/svg", name);
    }

    // Effective node position accounting for manual drag offset
    function getEffectivePos(nodeId) {
      return manualPositions.get(nodeId) || lastNodePositions.get(nodeId) || null;
    }

    function rebuildContainerIndex(visibleEntities) {
      const visibleIds = new Set(visibleEntities.map(entity => entity.id));
      const map = new Map();
      visibleEntities.forEach(entity => {
        if (!map.has(entity.id)) map.set(entity.id, []);
        if (Array.isArray(entity.children)) {
          for (const childIdRaw of entity.children) {
            const childId = typeof childIdRaw === "string" ? childIdRaw.trim() : "";
            if (!childId || childId === entity.id || !visibleIds.has(childId)) {
              continue;
            }
            const currentChildren = new Set(map.get(entity.id));
            currentChildren.add(childId);
            map.set(entity.id, Array.from(currentChildren));
            if (!map.has(childId)) map.set(childId, []);
          }
        }
        if (typeof entity.container === "string") {
          const containerId = entity.container.trim();
          if (!containerId || containerId === entity.id || !visibleIds.has(containerId)) {
            return;
          }
          const currentChildren = new Set(map.get(containerId));
          currentChildren.add(entity.id);
          map.set(containerId, Array.from(currentChildren));
          if (!map.has(entity.id)) map.set(entity.id, []);
        }
      });
      return map;
    }

    function getContainedChildren(entityId) {
      return containerIndex.get(entityId) || [];
    }

    function gatherDescendantIds(rootId) {
      const result = [];
      const seen = new Set([rootId]);
      const stack = [rootId];
      while (stack.length) {
        const currentId = stack.pop();
        const childIds = getContainedChildren(currentId);
        for (const childId of childIds) {
          if (typeof childId !== "string" || seen.has(childId)) {
            continue;
          }
          seen.add(childId);
          result.push(childId);
          stack.push(childId);
        }
      }
      return result;
    }

    function isContainerNode(entityId) {
      const children = getContainedChildren(entityId);
      return children.length > 0;
    }

    const SELECTION_RING_GAP = 6;
    const SELECTION_RING_STROKE_WIDTH = 3;
    const MIN_ARROW_LANDING_RUN = 18;
    function edgeNodeGap() {
      return SELECTION_RING_GAP + SELECTION_RING_STROKE_WIDTH + routingConfig.extraClearance;
    }

    // Simple straight-line path between two node bounding boxes during live drag.
    // Clip a straight line to node boundaries using proper rect intersection.
    // Returns an SVG path string that starts just outside srcPos and ends just
    // outside dstPos, so arrowheads sit at the target boundary and are not
    // hidden under node shapes.
    function simpleEdgePath(srcPos, dstPos) {
      const sx = srcPos.x + srcPos.width / 2;
      const sy = srcPos.y + srcPos.height / 2;
      const tx = dstPos.x + dstPos.width / 2;
      const ty = dstPos.y + dstPos.height / 2;
      const dx = tx - sx;
      const dy = ty - sy;
      const dist = Math.hypot(dx, dy);
      if (dist < 1) return `M ${sx} ${sy}`;
      const ndx = dx / dist;
      const ndy = dy / dist;

      // Distance from source center to source boundary along (ndx, ndy)
      let ts = Infinity;
      if (Math.abs(ndx) > 1e-6) ts = Math.min(ts, (srcPos.width  / 2) / Math.abs(ndx));
      if (Math.abs(ndy) > 1e-6) ts = Math.min(ts, (srcPos.height / 2) / Math.abs(ndy));
      const p0x = sx + ndx * (ts + edgeNodeGap());
      const p0y = sy + ndy * (ts + edgeNodeGap());

      // Distance from target center to target boundary along (-ndx, -ndy)
      let tt = Infinity;
      if (Math.abs(ndx) > 1e-6) tt = Math.min(tt, (dstPos.width  / 2) / Math.abs(ndx));
      if (Math.abs(ndy) > 1e-6) tt = Math.min(tt, (dstPos.height / 2) / Math.abs(ndy));
      const p1x = tx - ndx * (tt + edgeNodeGap());
      const p1y = ty - ndy * (tt + edgeNodeGap());

      return `M ${p0x} ${p0y} L ${p1x} ${p1y}`;
    }

    function manualDoglegPath(srcPos, dstPos, routeIndex = 0, routeCount = 1) {
      const sx = srcPos.x + srcPos.width / 2;
      const sy = srcPos.y + srcPos.height / 2;
      const tx = dstPos.x + dstPos.width / 2;
      const ty = dstPos.y + dstPos.height / 2;
      const dx = tx - sx;
      const dy = ty - sy;
      const routeOffset = (routeIndex - (routeCount - 1) / 2) * routingConfig.parallelSpacing;
      const mergeLane = edgeNodeGap() + Math.max(MIN_ARROW_LANDING_RUN, routingConfig.mergeLaneDistance);

      if (Math.abs(dx) >= Math.abs(dy)) {
        const sourceOnRight = dx >= 0;
        const startX = sourceOnRight ? srcPos.x + srcPos.width + edgeNodeGap() : srcPos.x - edgeNodeGap();
        const startY = sy + routeOffset;
        const endX = sourceOnRight ? dstPos.x - edgeNodeGap() : dstPos.x + dstPos.width + edgeNodeGap();
        const endY = ty + routeOffset;
        const laneX = sourceOnRight ? dstPos.x - mergeLane : dstPos.x + dstPos.width + mergeLane;
        return roundedPathForPoints([
          {x: startX, y: startY},
          {x: laneX, y: startY},
          {x: laneX, y: endY},
          {x: endX, y: endY}
        ]);
      }

      const sourceBelow = dy >= 0;
      const startX = sx + routeOffset;
      const startY = sourceBelow ? srcPos.y + srcPos.height + edgeNodeGap() : srcPos.y - edgeNodeGap();
      const endX = tx + routeOffset;
      const endY = sourceBelow ? dstPos.y - edgeNodeGap() : dstPos.y + dstPos.height + edgeNodeGap();
      const laneY = sourceBelow ? dstPos.y - mergeLane : dstPos.y + dstPos.height + mergeLane;
      return roundedPathForPoints([
        {x: startX, y: startY},
        {x: startX, y: laneY},
        {x: endX, y: laneY},
        {x: endX, y: endY}
      ]);
    }

    function isContainmentAncestor(ancestorId, descendantId) {
      const seen = new Set();
      let current = parentByNode.get(descendantId);
      while (current && !seen.has(current)) {
        if (current === ancestorId) return true;
        seen.add(current);
        current = parentByNode.get(current);
      }
      return false;
    }

    function pointOutsideRectToward(rect, point) {
      const cx = rect.x + rect.width / 2;
      const cy = rect.y + rect.height / 2;
      const dx = point.x - cx;
      const dy = point.y - cy;
      const distance = Math.hypot(dx, dy);
      if (distance < 1) return {x: cx, y: cy};
      const ndx = dx / distance;
      const ndy = dy / distance;
      let boundaryDistance = Infinity;
      if (Math.abs(ndx) > 1e-6) boundaryDistance = Math.min(boundaryDistance, (rect.width / 2) / Math.abs(ndx));
      if (Math.abs(ndy) > 1e-6) boundaryDistance = Math.min(boundaryDistance, (rect.height / 2) / Math.abs(ndy));
      return {
        x: cx + ndx * (boundaryDistance + edgeNodeGap()),
        y: cy + ndy * (boundaryDistance + edgeNodeGap()),
      };
    }

    function containmentInternalPath(containerPos, descendantPos, containerIsSource) {
      const targetCenterX = descendantPos.x + descendantPos.width / 2;
      const horizontalPadding = Math.min(24, containerPos.width / 4);
      const anchor = {
        x: Math.max(
          containerPos.x + horizontalPadding,
          Math.min(targetCenterX, containerPos.x + containerPos.width - horizontalPadding)
        ),
        y: containerPos.y + Math.min(44, Math.max(20, containerPos.height / 5)),
      };
      const descendantAnchor = pointOutsideRectToward(descendantPos, anchor);
      const points = containerIsSource
        ? [anchor, descendantAnchor]
        : [descendantAnchor, anchor];
      return roundedPathForPoints(points);
    }

    function routedPathForEndpoints(srcId, dstId, srcPos, dstPos, routeIndex = 0, routeCount = 1) {
      if (isContainmentAncestor(srcId, dstId)) {
        return containmentInternalPath(srcPos, dstPos, true);
      }
      if (isContainmentAncestor(dstId, srcId)) {
        return containmentInternalPath(dstPos, srcPos, false);
      }
      return manualDoglegPath(srcPos, dstPos, routeIndex, routeCount);
    }

    function incidentEdgePaths(nodeId) {
      return Array.from(new Set([
        ...edgeElementsForNode("data-source-node-id", nodeId),
        ...edgeElementsForNode("data-target-node-id", nodeId),
      ]))
        .filter(pathEl => pathEl.style.display !== "none");
    }

    function routeGroupKey(pathEl) {
      const src = pathEl.dataset.sourceNodeId || "";
      const dst = pathEl.dataset.targetNodeId || "";
      const a = src < dst ? src : dst;
      const b = src < dst ? dst : src;
      return `${a}::${b}`;
    }

    function rerouteEdgePathsFromCurrentPositions(paths) {
      const visiblePaths = Array.from(paths || [])
        .filter(pathEl => pathEl && pathEl.style.display !== "none");
      const uniquePaths = Array.from(new Set(visiblePaths));
      const routeCounts = new Map();
      uniquePaths.forEach(pathEl => {
        const key = routeGroupKey(pathEl);
        routeCounts.set(key, (routeCounts.get(key) || 0) + 1);
      });
      const routeSeen = new Map();
      uniquePaths.forEach(pathEl => {
        const srcId = pathEl.dataset.sourceNodeId;
        const dstId = pathEl.dataset.targetNodeId;
        const srcPos = getEffectivePos(srcId);
        const dstPos = getEffectivePos(dstId);
        if (!srcPos || !dstPos) return;
        const key = routeGroupKey(pathEl);
        const routeIndex = routeSeen.get(key) || 0;
        routeSeen.set(key, routeIndex + 1);
        pathEl.setAttribute("d", routedPathForEndpoints(srcId, dstId, srcPos, dstPos, routeIndex, routeCounts.get(key) || 1));
        syncArrowheadForPath(pathEl);
      });
    }

    function rerouteIncidentEdgesFromCurrentPositions(nodeId) {
      rerouteEdgePathsFromCurrentPositions(incidentEdgePaths(nodeId));
    }

    function rerouteAllVisibleEdgesFromCurrentPositions() {
      rerouteEdgePathsFromCurrentPositions(document.querySelectorAll(".edge-path"));
    }

    // Redraw all edges incident to a node (including bridges) to follow drag.
    function updateEdgesForNode(nodeId) {
      incidentEdgePaths(nodeId).forEach(pathEl => {
        const srcId = pathEl.dataset.sourceNodeId;
        const dstId = pathEl.dataset.targetNodeId;
        const srcPos = getEffectivePos(srcId);
        const dstPos = getEffectivePos(dstId);
        if (srcPos && dstPos) {
          pathEl.setAttribute("d", routedPathForEndpoints(srcId, dstId, srcPos, dstPos));
          syncArrowheadForPath(pathEl);
        }
      });
    }
