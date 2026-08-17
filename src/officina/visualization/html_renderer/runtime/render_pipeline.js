    // ── Full ELK-based layout/render ─────────────────────────────────────────

    async function updateVisibilityFull({preserveManualPositions = false} = {}) {
      renderHiddenNodes();
      const renderedEntities = docData.entities.filter(e => !isHiddenNode(e.id));
      const allEntities = docData.entities;
      const visibleEdges = computeVisibleEdges();
      const currentVersion = ++renderVersion;
      const previousNodePositions = lastNodePositions;
      const previousPresentationRenderState = snapshotPresentationNodesRenderState();
      containerIndex = rebuildContainerIndex(allEntities);

      if (renderedEntities.length === 0) {
        clearMathBeforeMutation(containerLayer);
        presentationNodeLayer.replaceChildren();
        clearMathBeforeMutation(nodeLayer);
        containerLayer.innerHTML = "";
        edgeLayer.innerHTML = "";
        nodeLayer.innerHTML = "";
        lastRenderedEdges = [];
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
        clearMathBeforeMutation(containerLayer);
        clearMathBeforeMutation(nodeLayer);
        presentationNodeLayer.replaceChildren();
        containerLayer.innerHTML = "";
        edgeLayer.innerHTML = "";
        nodeLayer.innerHTML = "";
        lastRenderedEdges = [];
        elkStatus.textContent = "";
        renderPresentationNodes();
        svgEl.setAttribute("width", String(graphWidth));
        svgEl.setAttribute("height", String(graphHeight));
        svgEl.setAttribute("viewBox", `${graphMinX} ${graphMinY} ${graphWidth} ${graphHeight}`);
        fitGraph();
        hasFittedOnce = true;

        const targetCounts = new Map();
        visibleEdges.forEach(edge => targetCounts.set(edge.target, (targetCounts.get(edge.target) || 0) + 1));

        (graph.edges || []).forEach((elkEdge) => {
          const idx = parseInt(elkEdge.id.replace("elk_edge_", ""), 10);
          const meta = visibleEdges[idx];
          const section = (elkEdge.sections || [])[0];
          if (!section || !meta) return;
          const points = offsetEdgeEndpoints(
            mergedTargetPoints(meta, pointsForSection(section), targetCounts),
            meta.source,
            meta.target
          );
          const path = createSvgElement("path");
          path.setAttribute("class", "edge-path");
          path.setAttribute("d", roundedPathForPoints(points));
          const edgeStyle = edgeStyleForType(meta.type);
          applyEdgeMetadataPresentation(path, meta, edgeStyle, edgeColorForTarget(meta.target));
          path.dataset.edgeId = meta.edge_id || elkEdge.id;
          path.dataset.targetNodeId = meta.target;
          path.dataset.sourceNodeId = meta.source;
          path.dataset.derived = meta.derived ? "true" : "false";
          path.dataset.edgeType = String(meta.type || "unknown");
          path.dataset.aggregate = meta.aggregate ? "true" : "false";
          path.dataset.bundle = meta.bundle ? "true" : "false";
          path.__edgeMeta = meta;
          edgeLayer.appendChild(path);
          syncEdgeMetadataPresentationGeometry(path);
          attachArrowhead(path);
          bindEdgeHover(path, meta);
          lastRenderedEdges.push(meta);
        });
        syncEdgePresentationLegend();

        renderedOrder.forEach(entityId => {
          const entity = entityMap.get(entityId);
          const positioned = lastNodePositions.get(entityId);
          if (!positioned) return;
          if (!entity) return;
          if (isHiddenNode(entityId)) return;
          const nodeEl = renderNode(entity, positioned);
          // Restore manual position as transform offset
          const manual = manualPositions.get(entityId);
          if (manual && !presentationGroupedNodeIds.has(entityId)) {
            const dx = manual.x - positioned.x;
            const dy = manual.y - positioned.y;
            if (dx !== 0 || dy !== 0) nodeEl.setAttribute("transform", `translate(${dx},${dy})`);
          }
          bindNodeInteractions(nodeEl, entity);
          (isContainerNode(entityId) ? containerLayer : nodeLayer).appendChild(nodeEl);
        });

        manualPositions.forEach((_, nodeId) => {
          if (!isHiddenNode(nodeId)) rerouteIncidentEdgesFromCurrentPositions(nodeId);
        });
        rerouteAllVisibleEdgesFromCurrentPositions();
        refreshEdgeOcclusionMasks();

        applyVisibilityPresentation();
        typesetElement(containerLayer);
        typesetElement(nodeLayer);

        // Restore selection highlight and details
        if (selectedPresentationNodeId) {
          showPresentationNodeDetails(selectedPresentationNodeId);
        } else if (selectedNodeIds.size) {
          syncSelectionPresentation();
          showSelectionDetails();
        } else {
          syncToolbar();
          rawJsonCodeEl.textContent = JSON.stringify(docData, null, 2);
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
