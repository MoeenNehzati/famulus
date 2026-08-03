    // ── Edge emphasis ────────────────────────────────────────────────────────

    function emphasizeEdge(pathEl, strokeColor = null) {
      if (!pathEl) return;
      const arrowEl = arrowForPath(pathEl);
      if (strokeColor) pathEl.style.stroke = strokeColor;
      pathEl.style.strokeWidth = String(Math.max(3, Number(pathEl.__edgeBaseStrokeWidth || 0) + 1));
      pathEl.style.opacity = "0.98";
      syncArrowheadForPath(pathEl);
    }

    function clearEdgeEmphasis(pathEl) {
      if (!pathEl) return;
      pathEl.style.stroke = "";
      pathEl.style.strokeWidth = pathEl.__edgeBaseStrokeWidth || "";
      pathEl.style.opacity = "";
      pathEl.style.filter = pathEl.__edgeBaseFilter || "";
      syncArrowheadForPath(pathEl);
    }

    function setIncomingEdgeHighlight(nodeId, active) {
      edgeElementsForNode("data-target-node-id", nodeId).forEach(pathEl => {
        if (active) emphasizeEdge(pathEl);
        else clearEdgeEmphasis(pathEl);
      });
    }

    // ── Event binding for edges/nodes ────────────────────────────────────────

    let hoveredEdgePath = null;
    let tooltipPositionFrame = null;
    let tooltipClientX = 0;
    let tooltipClientY = 0;
    let tooltipWidth = 320;
    let tooltipHeight = 120;

    function positionTooltip(event) {
      tooltipClientX = event.clientX;
      tooltipClientY = event.clientY;
      if (tooltipPositionFrame !== null) return;
      tooltipPositionFrame = requestAnimationFrame(() => {
        tooltipPositionFrame = null;
        const margin = 10;
        tooltip.style.left = `${Math.max(margin, Math.min(tooltipClientX + 16, window.innerWidth - tooltipWidth - margin))}px`;
        tooltip.style.top = `${Math.max(margin, Math.min(tooltipClientY + 16, window.innerHeight - tooltipHeight - margin))}px`;
      });
    }

    function showTooltip(event, content) {
      clearMathBeforeMutation(tooltip);
      tooltip.innerHTML = content;
      tooltip.style.display = "block";
      const bounds = tooltip.getBoundingClientRect();
      tooltipWidth = bounds.width || 320;
      tooltipHeight = bounds.height || 120;
      positionTooltip(event);
      typesetElement(tooltip);
    }

    function hideTooltip() {
      if (tooltipPositionFrame !== null) {
        cancelAnimationFrame(tooltipPositionFrame);
        tooltipPositionFrame = null;
      }
      nextMathGeneration(tooltip);
      tooltip.style.display = "none";
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
        showTooltip(event, edgeTooltipText(edge));
        emphasizeEdge(pathEl, "#1f2933");
      });
      pathEl.addEventListener("mousemove", event => {
        positionTooltip(event);
      });
      pathEl.addEventListener("mouseleave", () => {
        if (hoveredEdgePath !== pathEl) return;
        hoveredEdgePath = null;
        hideTooltip();
        clearEdgeEmphasis(pathEl);
        pathEl.style.stroke = baseColor;
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
        showTooltip(event, tooltipText(entity));
        nodeEl.classList.add("hovered");
        setIncomingEdgeHighlight(entity.id, true);
      });
      nodeEl.addEventListener("mousemove", event => {
        positionTooltip(event);
      });
      nodeEl.addEventListener("mouseleave", () => {
        hideTooltip();
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
        if (isContainerNode(entity.id) && event.altKey) {
          toggleContainerCollapsed(entity.id);
          return;
        }
        hideNodes([entity.id]);
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
