    // ── Full ELK-based layout/render ─────────────────────────────────────────

    let paintVersion = 0;
    let latestPaintPromise = Promise.resolve(), latestStructuralPromise = Promise.resolve();
    const lastEdgePaths = new Map();

    function edgeRouteStateKey(edge) {
      return JSON.stringify([routingConfig, getEffectivePos(edge.source), getEffectivePos(edge.target)]);
    }

    function reconciliationOperationCap(currentSize, desiredSize) {
      return currentSize > 0 && desiredSize * 2 <= currentSize ? 128 : 48;
    }

    function runPaintOperations(operations, version, operationCap = 48) {
      return new Promise(resolve => {
        let index = 0;
        const runChunk = () => {
          if (version !== paintVersion) { resolve(false); return; }
          const deadline = performance.now() + 6;
          let chunkSize = 0;
          while (version === paintVersion && index < operations.length
              && chunkSize < operationCap && performance.now() < deadline) {
            operations[index++]();
            chunkSize += 1;
          }
          if (version !== paintVersion) { resolve(false); return; }
          if (index < operations.length) {
            let resumed = false;
            const resume = () => {
              if (resumed) return;
              resumed = true;
              runChunk();
            };
            requestAnimationFrame(resume);
            setTimeout(resume, 16);
          }
          else resolve(version === paintVersion);
        };
        runChunk();
      });
    }

    function edgePaintKey(edge) {
      return String(edge.edge_id || `projection_${edge.source}_${edge.target}_${edge.type || "unknown"}`);
    }

    function createRenderedEdge(edge, pathData) {
      const path = createSvgElement("path");
      path.setAttribute("class", "edge-path");
      path.setAttribute("d", pathData);
      const edgeStyle = edgeStyleForType(edge.type);
      applyEdgeMetadataPresentation(path, edge, edgeStyle, edgeColorForTarget(edge.target));
      path.dataset.edgeId = edgePaintKey(edge);
      path.dataset.targetNodeId = edge.target;
      path.dataset.sourceNodeId = edge.source;
      path.dataset.derived = edge.derived ? "true" : "false";
      path.dataset.edgeType = String(edge.type || "unknown");
      path.dataset.aggregate = edge.aggregate ? "true" : "false";
      path.dataset.bundle = edge.bundle ? "true" : "false";
      path.dataset.edgeMetaKey = JSON.stringify(edge);
      path.__edgeMeta = edge;
      edgePresentationUnderlaysForPath(path).forEach(underlay => {
        underlay.dataset.edgeId = path.dataset.edgeId;
        edgeLayer.appendChild(underlay);
      });
      edgeLayer.appendChild(path);
      const routeSample = pathPointsForArrow(path);
      syncEdgeMetadataPresentationGeometry(path, routeSample);
      attachArrowhead(path, routeSample);
      bindEdgeHover(path, edge);
      return path;
    }

    function reconcileVisibleScene(renderedEntities, visibleEdges, renderedOrder) {
      const version = ++paintVersion;
      const operations = [];
      const desiredNodeIds = new Set(renderedEntities.map(entity => entity.id));
      const desiredEdgeIds = new Set(visibleEdges.map(edgePaintKey));
      const currentEdgePaths = Array.from(edgeLayer.querySelectorAll(".edge-path")).sort((a, b) =>
        String(a.dataset.edgeId).localeCompare(String(b.dataset.edgeId))
      );
      const operationCap = reconciliationOperationCap(
        nodeElementIndex.size + currentEdgePaths.length,
        desiredNodeIds.size + desiredEdgeIds.size,
      );

      Array.from(svgEl.querySelectorAll(".graph-node")).sort((a, b) =>
        String(a.dataset.nodeId).localeCompare(String(b.dataset.nodeId))
      ).forEach(node => {
        if (!desiredNodeIds.has(node.dataset.nodeId)) operations.push(() => {
          clearMathBeforeMutation(node);
          nodeElementIndex.delete(node.dataset.nodeId);
          node.remove();
        });
      });
      currentEdgePaths.forEach(path => {
        if (!desiredEdgeIds.has(path.dataset.edgeId)) operations.push(() => {
          lastEdgePaths.set(path.dataset.edgeId, {
            data: path.getAttribute("d") || "",
            state: path.dataset.routeState || "",
          });
          removeEdgePresentationResources(path);
          arrowForPath(path)?.remove();
          path.remove();
        });
      });

      const entityById = new Map(renderedEntities.map(entity => [entity.id, entity]));
      const orderedNodeIds = [], visitedNodeIds = new Set(), previousNodeByLayer = new Map();
      const visitNode = id => {
        if (!desiredNodeIds.has(id) || visitedNodeIds.has(id)) return;
        visitedNodeIds.add(id);
        visitNode(parentByNode.get(id));
        orderedNodeIds.push(id);
      };
      renderedOrder.forEach(visitNode);
      orderedNodeIds.forEach(entityId => {
        const entity = entityById.get(entityId);
        const position = lastNodePositions.get(entityId);
        if (!entity || !position) return;
        operations.push(() => {
          let node = nodeElement(entityId);
          const key = JSON.stringify([entity, position, nodePresentationState(entity).className]);
          if (node?.dataset.renderKey !== key) {
            if (node) { clearMathBeforeMutation(node); node.remove(); }
            node = renderNode(entity, position);
            node.dataset.renderKey = key;
            bindNodeInteractions(node, entity);
            typesetElement(node);
          }
          const manual = manualPositions.get(entityId);
          if (manual && !presentationGroupedNodeIds.has(entityId)) {
            const dx = manual.x - position.x;
            const dy = manual.y - position.y;
            if (dx || dy) node.setAttribute("transform", `translate(${dx},${dy})`);
          } else node.removeAttribute("transform");
          const layer = isContainerNode(entityId) ? containerLayer : nodeLayer;
          const previousNode = previousNodeByLayer.get(layer);
          const nextNode = previousNode ? previousNode.nextElementSibling : layer.firstElementChild;
          if (node.parentNode !== layer || node !== nextNode) {
            const focused = node.contains(document.activeElement) ? document.activeElement : null;
            layer.insertBefore(node, nextNode);
            focused?.focus({preventScroll: true});
          }
          previousNodeByLayer.set(layer, node);
          nodeElementIndex.set(entityId, node);
        });
      });

      const routeCounts = new Map();
      visibleEdges.forEach(edge => {
        const key = [edge.source, edge.target].sort().join("::");
        routeCounts.set(key, (routeCounts.get(key) || 0) + 1);
      });
      const routeSeen = new Map();
      visibleEdges.slice().sort((a, b) => edgePaintKey(a).localeCompare(edgePaintKey(b))).forEach(edge => {
        operations.push(() => {
          const srcPos = getEffectivePos(edge.source);
          const dstPos = getEffectivePos(edge.target);
          if (!srcPos || !dstPos) return;
          const routeKey = [edge.source, edge.target].sort().join("::");
          const routeIndex = routeSeen.get(routeKey) || 0;
          routeSeen.set(routeKey, routeIndex + 1);
          const pathData = routedPathForEndpoints(
            edge.source, edge.target, srcPos, dstPos, routeIndex, routeCounts.get(routeKey) || 1
          );
          const routeState = edgeRouteStateKey(edge);
          let path = edgeLayer.querySelector(`.edge-path[data-edge-id="${selectorValue(edgePaintKey(edge))}"]`);
          const cachedPath = lastEdgePaths.get(edgePaintKey(edge));
          const resolvedPathData = !path && cachedPath?.state === routeState ? cachedPath.data : pathData;
          const edgeMetaKey = JSON.stringify(edge);
          let geometryCurrent = false;
          if (!path || path.dataset.edgeMetaKey !== edgeMetaKey) {
            if (path) { removeEdgePresentationResources(path); arrowForPath(path)?.remove(); path.remove(); }
            path = createRenderedEdge(edge, resolvedPathData);
            geometryCurrent = true;
          }
          path.__edgeMeta = edge;
          path.dataset.routeState = routeState;
          if (!geometryCurrent && path.getAttribute("d") !== resolvedPathData) {
            path.setAttribute("d", resolvedPathData);
            syncEdgeRouteGeometry(path);
          }
        });
      });

      operations.push(() => {
        lastRenderedEdges = visibleEdges;
        syncEdgePresentationLegend();
        applyVisibilityPresentation(operations);
      });
      const paintPromise = runPaintOperations(operations, version, operationCap).then(current => {
        if (!current) return false;
        return currentMathTypesetTail().then(() => true);
      });
      if (version === paintVersion) latestPaintPromise = paintPromise;
      return paintPromise;
    }

    window.officinaRendererDiagnostics = {
      whenIdle: async () => {
        let observed, structural;
        do {
          structural = latestStructuralPromise;
          observed = latestPaintPromise;
          await Promise.all([structural, observed]);
        } while (structural !== latestStructuralPromise || observed !== latestPaintPromise);
      },
    };

    function updateVisibilityFull(options) { return latestStructuralPromise = performVisibilityFull(options); }
    async function performVisibilityFull({preserveManualPositions = false} = {}) {
      const renderedEntities = docData.entities.filter(e => !isHiddenNode(e.id));
      const allEntities = docData.entities;
      const visibleEdges = computeVisibleEdges();
      const currentVersion = ++renderVersion;
      const requestedPaintVersion = ++paintVersion;
      const previousNodePositions = lastNodePositions;
      const previousPresentationRenderState = snapshotPresentationNodesRenderState();
      containerIndex = rebuildContainerIndex(allEntities);

      if (renderedEntities.length === 0) {
        presentationNodeLayer.replaceChildren();
        await reconcileVisibleScene([], [], []);
        elkStatus.textContent = "No visible nodes.";
        svgEl.setAttribute("width", "800"); svgEl.setAttribute("height", "200");
        svgEl.setAttribute("viewBox", "0 0 800 200");
        commitPresentationNodesState();
        return true;
      }

      try {
        elkStatus.textContent = "Rendering graph layout...";
        const graph = await computeLayout(renderedEntities, visibleEdges);
        if (currentVersion !== renderVersion) return null;
        if (requestedPaintVersion !== paintVersion) {
          return updateVisibilityFull({preserveManualPositions});
        }
        lastNodePositions = new Map(lastNodePositions);
        const layoutNodes = [];
        flattenLayoutNodes(graph.children || [], 0, 0, layoutNodes);
        const layoutById = new Map(layoutNodes.map((node) => [node.id, node]));
        const renderedOrder = [];
        const renderedOrderSet = new Set();
        layoutNodes.forEach((node) => {
          if (renderedOrderSet.has(node.id)) return;
          renderedOrder.push(node.id);
          renderedOrderSet.add(node.id);
        });
        allEntities.forEach(entity => {
          const positioned = layoutById.get(entity.id);
          if (positioned) {
            lastNodePositions.set(entity.id, {
              x: positioned.x || 0,
              y: positioned.y || 0,
              width: positioned.width || defaultNodeDimensions(entity.id).width,
              height: positioned.height || defaultNodeDimensions(entity.id).height
            });
            if (!renderedOrderSet.has(entity.id)) {
              renderedOrder.push(entity.id);
              renderedOrderSet.add(entity.id);
            }
          }
        });
        allEntities.forEach(entity => {
          if (lastNodePositions.has(entity.id)) {
            return;
          }
          const containerId = typeof entity.container === "string" ? entity.container.trim() : "";
          const containerPos = containerId ? lastNodePositions.get(containerId) : null;
          if (containerPos && containerId) {
            const siblings = allEntities.filter(candidate => (
              typeof candidate.container === "string" &&
              candidate.container.trim() === containerId
            ));
            const index = siblings.findIndex(candidate => candidate.id === entity.id);
            const fallbackX = containerPos.x + 14;
            const fallbackY = containerPos.y + 76 + Math.max(index, 0) * 78;
            const fallbackDimensions = defaultNodeDimensions(entity.id);
            lastNodePositions.set(entity.id, {
              x: fallbackX,
              y: fallbackY,
              width: fallbackDimensions.width,
              height: fallbackDimensions.height,
            });
            if (!renderedOrderSet.has(entity.id)) {
              renderedOrder.push(entity.id);
            renderedOrderSet.add(entity.id);
            }
            return;
          }
          // fallback layout for nodes not returned by ELK
          if (!renderedOrderSet.has(entity.id)) {
            renderedOrder.push(entity.id);
            renderedOrderSet.add(entity.id);
          }
          const fallbackX = 80 + ((entity.position || 0) % 6) * 240;
          const fallbackY = 80 + Math.floor((entity.position || 0) / 6) * 108;
          const fallbackDimensions = defaultNodeDimensions(entity.id);
          lastNodePositions.set(entity.id, {
            x: fallbackX,
            y: fallbackY,
            width: fallbackDimensions.width,
            height: fallbackDimensions.height,
          });
        });
        applyPresentationNodesLayout(renderedEntities);
        renderedEntities.forEach((entity) => {
          const isChildNode = typeof entity.container === "string" && entity.container.trim().length > 0;
          if (
            (isChildNode || isContainerNode(entity.id)) &&
            !presentationGroupedNodeIds.has(entity.id) &&
            !presentationRestoringManualPositions &&
            !preserveManualPositions
          ) {
            manualPositions.delete(entity.id);
          }
        });
        hasFullLayout = true;

        const visibleShellBounds = presentationNodeComponents
          .filter(component => !hiddenPresentationNodes.has(component.presentationNodeId))
          .map(presentationComponentDisplayBounds);
        const committedBounds = [...Array.from(lastNodePositions.values()), ...visibleShellBounds];
        const graphMinX = Math.min(0, ...committedBounds.map(pos => pos.x - 40));
        const graphMinY = Math.min(0, ...committedBounds.map(pos => pos.y - 40));
        const graphMaxX = Math.max(900, ...committedBounds.map(pos => pos.x + pos.width + 80));
        const graphMaxY = Math.max(500, ...committedBounds.map(pos => pos.y + pos.height + 80));
        const graphWidth = graphMaxX - graphMinX;
        const graphHeight = graphMaxY - graphMinY;
        presentationNodeLayer.replaceChildren();
        elkStatus.textContent = "";
        renderPresentationNodes();
        svgEl.setAttribute("width", String(graphWidth));
        svgEl.setAttribute("height", String(graphHeight));
        svgEl.setAttribute("viewBox", `${graphMinX} ${graphMinY} ${graphWidth} ${graphHeight}`);
        fitGraph();
        hasFittedOnce = true;

        const paintCurrent = await reconcileVisibleScene(renderedEntities, visibleEdges, renderedOrder);
        if (!paintCurrent || currentVersion !== renderVersion) return null;
        // Restore selection highlight and details
        if (selectedPresentationNodeId) {
          showPresentationNodeDetails(selectedPresentationNodeId);
        } else if (selectedNodeIds.size) {
          syncSelectionPresentation();
          showSelectionDetails();
        } else {
          syncToolbar();
          showGraphDocumentJson();
        }
        const renderedNodeCount = svgEl.querySelectorAll(".graph-node").length;
        presentationRestoringManualPositions = false;
        if (renderedNodeCount === 0) {
          elkStatus.textContent = "No nodes were rendered.";
        } else {
          elkStatus.textContent = "";
        }
        commitPresentationNodesState();
        return true;
      } catch (error) {
        lastNodePositions = previousNodePositions;
        restorePresentationNodesRenderState(previousPresentationRenderState);
        rollbackPresentationNodesState();
        saveViewerState();
        elkStatus.textContent = `ELK layout failed: ${error.message || error}`;
        return false;
      }
    }
