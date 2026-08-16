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

    function cubicPathBetween(start, end) {
      const dx = end.x - start.x;
      const dy = end.y - start.y;
      const curvature = routingConfig.bezierCurvature / 100;
      if (Math.abs(dx) >= Math.abs(dy)) {
        const direction = dx >= 0 ? 1 : -1;
        const handle = Math.max(12, Math.min(320, Math.abs(dx) * curvature));
        return `M ${start.x} ${start.y} C ${start.x + direction * handle} ${start.y}, ${end.x - direction * handle} ${end.y}, ${end.x} ${end.y}`;
      }
      const direction = dy >= 0 ? 1 : -1;
      const handle = Math.max(12, Math.min(320, Math.abs(dy) * curvature));
      return `M ${start.x} ${start.y} C ${start.x} ${start.y + direction * handle}, ${end.x} ${end.y - direction * handle}, ${end.x} ${end.y}`;
    }

    function polylinePathBetween(start, end) {
      const dx = end.x - start.x;
      const dy = end.y - start.y;
      const bend = routingConfig.polylineBend / 100;
      const midpoint = Math.abs(dx) >= Math.abs(dy)
        ? {x: start.x + dx * bend, y: start.y}
        : {x: start.x, y: start.y + dy * bend};
      return `M ${start.x} ${start.y} L ${midpoint.x} ${midpoint.y} L ${end.x} ${end.y}`;
    }

    function splinePathBetween(start, end) {
      const dx = end.x - start.x;
      const dy = end.y - start.y;
      const tension = routingConfig.splineTension / 100;
      const midpoint = {x: (start.x + end.x) / 2, y: (start.y + end.y) / 2};
      if (Math.abs(dx) >= Math.abs(dy)) {
        const handle = Math.max(10, Math.min(180, Math.abs(dx) * tension));
        const direction = dx >= 0 ? 1 : -1;
        return `M ${start.x} ${start.y} C ${start.x + direction * handle} ${start.y}, ${midpoint.x - direction * handle} ${midpoint.y}, ${midpoint.x} ${midpoint.y} C ${midpoint.x + direction * handle} ${midpoint.y}, ${end.x - direction * handle} ${end.y}, ${end.x} ${end.y}`;
      }
      const handle = Math.max(10, Math.min(180, Math.abs(dy) * tension));
      const direction = dy >= 0 ? 1 : -1;
      return `M ${start.x} ${start.y} C ${start.x} ${start.y + direction * handle}, ${midpoint.x} ${midpoint.y - direction * handle}, ${midpoint.x} ${midpoint.y} C ${midpoint.x} ${midpoint.y + direction * handle}, ${end.x} ${end.y - direction * handle}, ${end.x} ${end.y}`;
    }

    function straightPathBetween(start, end) {
      return `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
    }

    function routedEndpointPair(srcPos, dstPos, routeIndex = 0, routeCount = 1) {
      const sx = srcPos.x + srcPos.width / 2;
      const sy = srcPos.y + srcPos.height / 2;
      const tx = dstPos.x + dstPos.width / 2;
      const ty = dstPos.y + dstPos.height / 2;
      const dx = tx - sx;
      const dy = ty - sy;
      const routeOffset = (routeIndex - (routeCount - 1) / 2) * routingConfig.parallelSpacing;
      if (Math.abs(dx) >= Math.abs(dy)) {
        const sourceOnRight = dx >= 0;
        return {
          start: {
            x: sourceOnRight ? srcPos.x + srcPos.width : srcPos.x,
            y: sy + routeOffset,
          },
          end: {
            x: sourceOnRight ? dstPos.x : dstPos.x + dstPos.width,
            y: ty + routeOffset,
          },
        };
      }
      const sourceBelow = dy >= 0;
      return {
        start: {
          x: sx + routeOffset,
          y: sourceBelow ? srcPos.y + srcPos.height : srcPos.y,
        },
        end: {
          x: tx + routeOffset,
          y: sourceBelow ? dstPos.y : dstPos.y + dstPos.height,
        },
      };
    }

    function pathBetweenForGeometry(start, end) {
      if (routingConfig.geometry === "polyline") return polylinePathBetween(start, end);
      if (routingConfig.geometry === "spline") return splinePathBetween(start, end);
      if (routingConfig.geometry === "bezier") return cubicPathBetween(start, end);
      if (routingConfig.geometry === "straight") return straightPathBetween(start, end);
      return roundedPathForPoints([start, end]);
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

    function pointOutsideRectToward(rect, point, gap = edgeNodeGap()) {
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
        x: cx + ndx * (boundaryDistance + gap),
        y: cy + ndy * (boundaryDistance + gap),
      };
    }

    function containmentInternalPath(containerPos, descendantPos, containerIsSource) {
      const descendantCenterX = descendantPos.x + descendantPos.width / 2;
      const descendantCenterY = descendantPos.y + descendantPos.height / 2;
      const containerRight = containerPos.x + containerPos.width;
      const containerBottom = containerPos.y + containerPos.height;
      const descendantRight = descendantPos.x + descendantPos.width;
      const descendantBottom = descendantPos.y + descendantPos.height;
      const candidates = [
        {
          clearance: descendantPos.x - containerPos.x,
          containerAnchor: {x: containerPos.x, y: descendantCenterY},
          descendantAnchor: {x: descendantPos.x, y: descendantCenterY},
        },
        {
          clearance: containerRight - descendantRight,
          containerAnchor: {x: containerRight, y: descendantCenterY},
          descendantAnchor: {x: descendantRight, y: descendantCenterY},
        },
        {
          clearance: containerBottom - descendantBottom,
          containerAnchor: {x: descendantCenterX, y: containerBottom},
          descendantAnchor: {x: descendantCenterX, y: descendantBottom},
        },
        {
          clearance: descendantPos.y - containerPos.y,
          headerPenalty: 80,
          containerAnchor: {x: descendantCenterX, y: containerPos.y},
          descendantAnchor: {x: descendantCenterX, y: descendantPos.y},
        },
      ].filter(candidate => candidate.clearance >= 0);
      candidates.sort((left, right) =>
        left.clearance + (left.headerPenalty || 0) - right.clearance - (right.headerPenalty || 0)
      );
      const {containerAnchor, descendantAnchor} = candidates[0];
      const points = containerIsSource
        ? [containerAnchor, descendantAnchor]
        : [descendantAnchor, containerAnchor];
      return pathBetweenForGeometry(points[0], points[1]);
    }

    function routedPathForEndpoints(srcId, dstId, srcPos, dstPos, routeIndex = 0, routeCount = 1) {
      if (isContainmentAncestor(srcId, dstId)) {
        return containmentInternalPath(srcPos, dstPos, true);
      }
      if (isContainmentAncestor(dstId, srcId)) {
        return containmentInternalPath(dstPos, srcPos, false);
      }
      if (routingConfig.geometry === "orthogonal") {
        return manualDoglegPath(srcPos, dstPos, routeIndex, routeCount);
      }
      const {start, end} = routedEndpointPair(srcPos, dstPos, routeIndex, routeCount);
      return pathBetweenForGeometry(start, end);
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
        syncEdgeMetadataPresentationGeometry(pathEl);
        syncArrowheadForPath(pathEl);
      });
    }

    function rerouteIncidentEdgesFromCurrentPositions(nodeId) {
      rerouteEdgePathsFromCurrentPositions(incidentEdgePaths(nodeId));
    }

    function rerouteAllVisibleEdgesFromCurrentPositions() {
      rerouteEdgePathsFromCurrentPositions(document.querySelectorAll(".edge-path"));
    }

    // Redraw all edges incident to a node, including derived projections, after drag.
    function updateEdgesForNode(nodeId) {
      incidentEdgePaths(nodeId).forEach(pathEl => {
        const srcId = pathEl.dataset.sourceNodeId;
        const dstId = pathEl.dataset.targetNodeId;
        const srcPos = getEffectivePos(srcId);
        const dstPos = getEffectivePos(dstId);
        if (srcPos && dstPos) {
          pathEl.setAttribute("d", routedPathForEndpoints(srcId, dstId, srcPos, dstPos));
          syncEdgeMetadataPresentationGeometry(pathEl);
          syncArrowheadForPath(pathEl);
        }
      });
    }

    function svgBoundsForElement(element) {
      const screenMatrix = svgEl.getScreenCTM();
      if (!screenMatrix) return null;
      const inverse = screenMatrix.inverse();
      const rect = element.getBoundingClientRect();
      const corners = [
        [rect.left, rect.top], [rect.right, rect.top],
        [rect.right, rect.bottom], [rect.left, rect.bottom],
      ].map(([x, y]) => {
        const point = svgEl.createSVGPoint();
        point.x = x;
        point.y = y;
        return point.matrixTransform(inverse);
      });
      const xs = corners.map(point => point.x);
      const ys = corners.map(point => point.y);
      return {
        x: Math.min(...xs),
        y: Math.min(...ys),
        width: Math.max(...xs) - Math.min(...xs),
        height: Math.max(...ys) - Math.min(...ys),
      };
    }

    function appendEdgeMaskBlocker(mask, bounds, padding = 0, radius = 2, fill = "black") {
      if (!bounds || bounds.width <= 0 || bounds.height <= 0) return;
      const blocker = createSvgElement("rect");
      blocker.setAttribute("x", String(bounds.x - padding));
      blocker.setAttribute("y", String(bounds.y - padding));
      blocker.setAttribute("width", String(bounds.width + 2 * padding));
      blocker.setAttribute("height", String(bounds.height + 2 * padding));
      blocker.setAttribute("rx", String(radius));
      blocker.setAttribute("fill", fill);
      mask.appendChild(blocker);
    }

    function appendNodeEdgeMaskBlocker(mask, occluder) {
      const shape = occluder.nodeEl?.querySelector(".node-shape");
      if (!shape) {
        appendEdgeMaskBlocker(
          mask,
          occluder.position,
          0,
          2,
          occluder.isContainer ? "#141414" : "black"
        );
        return;
      }
      const blocker = shape.cloneNode(false);
      const fill = occluder.isContainer ? "#141414" : "black";
      blocker.removeAttribute("class");
      blocker.removeAttribute("style");
      blocker.setAttribute("fill", fill);
      blocker.setAttribute("stroke", fill);
      blocker.removeAttribute("stroke-dasharray");
      blocker.setAttribute("pointer-events", "none");
      blocker.dataset.edgeOcclusionNodeId = occluder.id;
      const transform = occluder.nodeEl.getAttribute("transform");
      if (transform) blocker.setAttribute("transform", transform);
      mask.appendChild(blocker);
    }

    function boundsIntersect(left, right, padding = 0) {
      if (!left || !right) return false;
      return left.x <= right.x + right.width + padding
        && left.x + left.width >= right.x - padding
        && left.y <= right.y + right.height + padding
        && left.y + left.height >= right.y - padding;
    }

    function refreshEdgeOcclusionMasks() {
      const defs = svgEl.querySelector("defs");
      if (!defs) return;
      defs.querySelectorAll("[data-edge-occlusion-mask]").forEach(mask => mask.remove());
      const visibleNodeOccluders = docData.entities
        .map(entity => entity.id)
        .filter(nodeId => !isHiddenNode(nodeId) && getEffectivePos(nodeId))
        .map(nodeId => {
          const nodeEl = nodeElement(nodeId);
          return {
            id: nodeId,
            nodeEl,
            position: getEffectivePos(nodeId),
            isContainer: isContainerNode(nodeId),
            textBounds: nodeEl
              ? Array.from(nodeEl.querySelectorAll(".node-label, .node-subtitle"))
                .map(svgBoundsForElement)
                .filter(Boolean)
              : [],
          };
        });

      edgeLayer.querySelectorAll(".edge-path").forEach((pathEl, index) => {
        if (pathEl.style.display === "none") return;
        const pathBounds = pathEl.getBBox();
        const maskPadding = 16;
        const maskBounds = {
          x: pathBounds.x - maskPadding,
          y: pathBounds.y - maskPadding,
          width: Math.max(1, pathBounds.width + 2 * maskPadding),
          height: Math.max(1, pathBounds.height + 2 * maskPadding),
        };
        const sourceId = String(pathEl.dataset.sourceNodeId || "");
        const targetId = String(pathEl.dataset.targetNodeId || "");
        const mask = createSvgElement("mask");
        const maskId = `edge-occlusion-${renderVersion}-${index}`;
        mask.setAttribute("id", maskId);
        mask.setAttribute("maskUnits", "userSpaceOnUse");
        mask.setAttribute("x", String(maskBounds.x));
        mask.setAttribute("y", String(maskBounds.y));
        mask.setAttribute("width", String(maskBounds.width));
        mask.setAttribute("height", String(maskBounds.height));
        mask.dataset.edgeOcclusionMask = "true";
        const background = createSvgElement("rect");
        background.setAttribute("x", String(maskBounds.x));
        background.setAttribute("y", String(maskBounds.y));
        background.setAttribute("width", String(maskBounds.width));
        background.setAttribute("height", String(maskBounds.height));
        background.setAttribute("fill", "white");
        mask.appendChild(background);

        visibleNodeOccluders.forEach(occluder => {
          const isEndpoint = occluder.id === sourceId || occluder.id === targetId;
          if (!isEndpoint) {
            if (boundsIntersect(pathBounds, occluder.position, 8)) {
              appendNodeEdgeMaskBlocker(mask, occluder);
            }
          }
          occluder.textBounds.forEach(textBounds => {
            if (boundsIntersect(pathBounds, textBounds, 8)) {
              appendEdgeMaskBlocker(mask, textBounds, 3, 3);
            }
          });
        });
        defs.appendChild(mask);
        const maskReference = `url(#${maskId})`;
        pathEl.setAttribute("mask", maskReference);
        const arrow = arrowForPath(pathEl);
        if (arrow) arrow.setAttribute("mask", maskReference);
      });
    }
