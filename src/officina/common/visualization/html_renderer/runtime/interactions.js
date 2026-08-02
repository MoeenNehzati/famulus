    // ── Edge emphasis ────────────────────────────────────────────────────────

    function emphasizeEdge(pathEl, strokeColor = null) {
      if (!pathEl) return;
      const arrowEl = arrowForPath(pathEl);
      if (strokeColor) pathEl.style.stroke = strokeColor;
      pathEl.style.strokeWidth = "3";
      pathEl.style.opacity = "0.98";
      pathEl.style.filter = "drop-shadow(0 0 1px rgba(17, 24, 39, 0.18))";
      syncArrowheadForPath(pathEl);
    }

    function clearEdgeEmphasis(pathEl) {
      if (!pathEl) return;
      pathEl.style.stroke = "";
      pathEl.style.strokeWidth = "";
      pathEl.style.opacity = "";
      pathEl.style.filter = "";
      syncArrowheadForPath(pathEl);
    }

    function setIncomingEdgeHighlight(nodeId, active) {
      edgeElementsForNode("data-target-node-id", nodeId).forEach(pathEl => {
        if (active) emphasizeEdge(pathEl);
        else clearEdgeEmphasis(pathEl);
      });
      if (!active) applyAncestorFocus();
    }

    // ── Event binding for edges/nodes ────────────────────────────────────────

    let hoveredEdgePath = null;

    function positionTooltip(event) {
      const margin = 10;
      const width = tooltip.offsetWidth || 320;
      const height = tooltip.offsetHeight || 120;
      tooltip.style.left = `${Math.max(margin, Math.min(event.clientX + 16, window.innerWidth - width - margin))}px`;
      tooltip.style.top = `${Math.max(margin, Math.min(event.clientY + 16, window.innerHeight - height - margin))}px`;
    }

    function bindEdgeHover(pathEl, edge) {
      pathEl.setAttribute("tabindex", "0");
      pathEl.setAttribute("role", "button");
      pathEl.setAttribute("aria-label", edgeTooltipText(edge));
      pathEl.addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        pathEl.dispatchEvent(new MouseEvent("click", {bubbles: true}));
      });
      const baseColor = pathEl.getAttribute("stroke") || edgeColorForTarget(edge.target);
      pathEl.addEventListener("mouseenter", event => {
        if (hoveredEdgePath && hoveredEdgePath !== pathEl) {
          clearEdgeEmphasis(hoveredEdgePath);
        }
        hoveredEdgePath = pathEl;
        clearMathBeforeMutation(tooltip);
        tooltip.innerHTML = edgeTooltipText(edge);
        positionTooltip(event);
        tooltip.style.display = "block";
        emphasizeEdge(pathEl, "#1f2933");
        typesetElement(tooltip);
      });
      pathEl.addEventListener("mousemove", event => {
        positionTooltip(event);
      });
      pathEl.addEventListener("mouseleave", () => {
        if (hoveredEdgePath !== pathEl) return;
        hoveredEdgePath = null;
        nextMathGeneration(tooltip);
        tooltip.style.display = "none";
        clearEdgeEmphasis(pathEl);
        pathEl.style.stroke = baseColor;
        applyAncestorFocus();
      });
      pathEl.addEventListener("click", event => {
        event.stopPropagation();
        showEdgeDetails(edge);
      });
    }

    function bindNodeInteractions(nodeEl, entity) {
      nodeEl.setAttribute("tabindex", "0");
      nodeEl.setAttribute("role", "button");
      nodeEl.setAttribute("aria-label", tooltipText(entity));
      nodeEl.addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        nodeEl.dispatchEvent(new MouseEvent("click", {bubbles: true}));
      });
      nodeEl.addEventListener("mouseenter", event => {
        if (draggingNodeId) return;
        if (hoveredEdgePath) {
          clearEdgeEmphasis(hoveredEdgePath);
          hoveredEdgePath = null;
        }
        clearMathBeforeMutation(tooltip);
        tooltip.innerHTML = tooltipText(entity);
        positionTooltip(event);
        tooltip.style.display = "block";
        typesetElement(tooltip);
        nodeEl.classList.add("hovered");
        setIncomingEdgeHighlight(entity.id, true);
      });
      nodeEl.addEventListener("mousemove", event => {
        positionTooltip(event);
      });
      nodeEl.addEventListener("mouseleave", () => {
        nextMathGeneration(tooltip);
        tooltip.style.display = "none";
        nodeEl.classList.remove("hovered");
        setIncomingEdgeHighlight(entity.id, false);
      });
      nodeEl.addEventListener("click", event => {
        if (nodeDragMoved) return; // suppress click after drag
        const additiveSelection = event.ctrlKey || event.metaKey;
        if (nodeClickTimer) clearTimeout(nodeClickTimer);
        nodeClickTimer = setTimeout(() => {
          nodeClickTimer = null;
          if (additiveSelection) toggleNodeSelection(entity.id);
          else setNodeSelection([entity.id], entity.id, "explicit");
        }, 180);
      });
      nodeEl.addEventListener("dblclick", event => {
        event.preventDefault();
        if (nodeClickTimer) {
          clearTimeout(nodeClickTimer);
          nodeClickTimer = null;
        }
        if (isContainerNode(entity.id)) {
          if (collapsedContainers.has(entity.id)) collapsedContainers.delete(entity.id);
          else collapsedContainers.add(entity.id);
          manualPositions.clear();
          hasFittedOnce = false;
          saveViewerState();
          updateVisibilityFull();
          return;
        }
        hiddenNodes.add(entity.id);
        dimmedNodes.delete(entity.id);
        if (selectedNodeIds.has(entity.id)) removeNodesFromSelection([entity.id]);
        if (focusNodeId === entity.id) {
          focusNodeId = null;
          if (ancestorFocusMode > 0) ancestorFocusMode = 0;
        }
        saveViewerState();
        updateVisibilityFast();
      });
      // Node drag (mousedown)
      nodeEl.addEventListener("mousedown", event => {
        if (event.button !== 0) return;
        draggingNodeId = entity.id;
        draggingNodeIds = [entity.id, ...gatherDescendantIds(entity.id)];
        draggingNodeOffsets = new Map();
        nodeDragMoved = false;
        dragStartClientX = event.clientX;
        dragStartClientY = event.clientY;
        draggingNodeIds.forEach((nodeId) => {
          const cur = manualPositions.get(nodeId);
          const orig = lastNodePositions.get(nodeId) || {x: 0, y: 0};
          draggingNodeOffsets.set(nodeId, {
            offsetX: cur ? cur.x - orig.x : 0,
            offsetY: cur ? cur.y - orig.y : 0
          });
          const childNodeEl = nodeElement(nodeId);
          if (childNodeEl) childNodeEl.classList.add("dragging-node");
        });
        event.stopPropagation();
      });
    }
