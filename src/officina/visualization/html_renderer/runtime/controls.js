    // ── Node dragging (SVG mouse events) ─────────────────────────────────────

    document.addEventListener("mousemove", event => {
      if (isPanning) {
        panX = panStartX + (event.clientX - panStartClientX);
        panY = panStartY + (event.clientY - panStartClientY);
        applyTransform();
        return;
      }
      if (draggingNodeId === null) return;
      const clientDx = event.clientX - dragStartClientX;
      const clientDy = event.clientY - dragStartClientY;
      if (!nodeDragMoved && Math.hypot(clientDx, clientDy) < DRAG_THRESHOLD) return;
      nodeDragMoved = true;
      tooltip.style.display = "none";

      // Convert client delta to SVG coordinate delta
      const rect = svgEl.getBoundingClientRect();
      const viewBox = svgEl.viewBox.baseVal;
      const scaleX = viewBox.width / rect.width;
      const scaleY = viewBox.height / rect.height;
      const svgDx = clientDx * scaleX;
      const svgDy = clientDy * scaleY;
      const impacted = new Set();
      draggingNodeIds.forEach((nodeId) => {
        const offsets = draggingNodeOffsets.get(nodeId) || { offsetX: 0, offsetY: 0 };
        const nodeEl = nodeElement(nodeId);
        const origPos = lastNodePositions.get(nodeId);
        if (!origPos) return;
        const offsetX = offsets.offsetX + svgDx;
        const offsetY = offsets.offsetY + svgDy;
        if (nodeEl) {
          nodeEl.setAttribute("transform", `translate(${offsetX},${offsetY})`);
        }
        manualPositions.set(nodeId, {
          x: origPos.x + offsetX,
          y: origPos.y + offsetY,
          width: origPos.width,
          height: origPos.height
        });
        impacted.add(nodeId);
      });

      const impactedPaths = [];
      impacted.forEach((nodeId) => {
        impactedPaths.push(...incidentEdgePaths(nodeId));
      });
      rerouteEdgePathsFromCurrentPositions(impactedPaths);
    });

    document.addEventListener("mouseup", event => {
      if (isPanning) {
        isPanning = false;
        canvasWrapEl.classList.remove("panning");
        saveViewerState();
        return;
      }
      if (draggingNodeId !== null) {
        const droppedNodeId = draggingNodeId;
        draggingNodeIds.forEach((nodeId) => {
          const nodeEl = nodeElement(nodeId);
          if (nodeEl) nodeEl.classList.remove("dragging-node");
        });
        if (nodeDragMoved) {
          draggingNodeIds.forEach((nodeId) => {
            rerouteIncidentEdgesFromCurrentPositions(nodeId);
          });
          refreshEdgeOcclusionMasks();
          saveViewerState();
        }
        draggingNodeIds = [];
        draggingNodeOffsets = new Map();
        draggingNodeId = null;
        // Keep nodeDragMoved true briefly to suppress the click event
        setTimeout(() => { nodeDragMoved = false; }, 0);
      }
    });

    // ── Toolbar button handlers ───────────────────────────────────────────────

    function handleHideSelected() {
      const nodeIds = visibleSelectedNodeIds();
      if (!nodeIds.length) return;
      hideNodes(nodeIds);
    }

    function handleDimSelected() {
      const nodeIds = visibleSelectedNodeIds();
      if (!nodeIds.length) return;
      toggleDimNodes(nodeIds);
    }

    function handleHideUnselected() {
      const nodeIds = visibleUnselectedNodeIds({preserveSelectionAncestors: true});
      if (!visibleSelectedNodeIds().length || !nodeIds.length) return;
      hideNodes(nodeIds);
    }

    function handleDimUnselected() {
      const nodeIds = visibleUnselectedNodeIds();
      if (!visibleSelectedNodeIds().length || !nodeIds.length) return;
      toggleDimNodes(nodeIds);
    }

    hideSelectedBtn.addEventListener("click", handleHideSelected);
    dimSelectedBtn.addEventListener("click", handleDimSelected);
    hideUnselectedBtn.addEventListener("click", handleHideUnselected);
    dimUnselectedBtn.addEventListener("click", handleDimUnselected);

    document.getElementById("redraw-btn").addEventListener("click", () => {
      manualPositions.clear();
      hasFittedOnce = false;
      svgEl.querySelectorAll(".graph-node").forEach(el => el.removeAttribute("transform"));
      updateVisibilityFull();
      saveViewerState();
    });

    function syncLegendRows() {
      syncNodeLegendRows();
      syncEdgeLegendRows();
    }

    function resetViewState({includeCategories = false} = {}) {
      if (includeCategories) resetFilteringState();
      clearPresentationNodesState();
      hiddenNodes.clear();
      dimmedNodes.clear();
      collapsedContainers.clear();
      selectedNodeIds.clear();
      selectedNodeId = null;
      selectionSource = "explicit";
      manualPositions.clear();
      hasFittedOnce = false;
      showSelectionDetails();
      if (includeCategories) {
        syncLegendRows();
      }
      if (includeCategories) localStorage.removeItem(viewerStateKey);
      else saveViewerState();
      updateVisibilityFull();
    }

    const resetBtn = document.getElementById("reset-btn");
    let resetClickTimer = null;
    resetBtn.addEventListener("click", () => {
      if (resetClickTimer) clearTimeout(resetClickTimer);
      resetClickTimer = setTimeout(() => {
        resetClickTimer = null;
        resetViewState({includeCategories: false});
      }, 180);
    });
    resetBtn.addEventListener("dblclick", event => {
      event.preventDefault();
      if (resetClickTimer) {
        clearTimeout(resetClickTimer);
        resetClickTimer = null;
      }
      resetViewState({includeCategories: true});
    });

    document.getElementById("zoom-in-btn").addEventListener("click", () => {
      zoomTowardContent(zoomLevel * 1.3);
      saveViewerState();
    });

    document.getElementById("zoom-out-btn").addEventListener("click", () => {
      zoomTowardContent(zoomLevel / 1.3);
      saveViewerState();
    });

    document.getElementById("fit-btn").addEventListener("click", () => {
      fitGraph();
      saveViewerState();
    });

    // ── Sidebar drag-to-reorder ───────────────────────────────────────────────

    let dragSrcSection = null;
    panelContent.addEventListener("dragstart", event => {
      const handle = event.target.closest(".drag-handle");
      if (!handle) { event.preventDefault(); return; }
      const section = handle.closest(".sidebar-section");
      if (!section || section.parentElement !== panelContent) { event.preventDefault(); return; }
      dragSrcSection = section;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", section.dataset.sectionId);
      section.classList.add("dragging");
    });
    panelContent.addEventListener("dragend", () => {
      panelContent.querySelectorAll(".sidebar-section.dragging").forEach(el => el.classList.remove("dragging"));
      panelContent.querySelectorAll(".sidebar-section.drag-over").forEach(el => el.classList.remove("drag-over"));
      dragSrcSection = null;
      saveSidebarOrder();
    });
    panelContent.addEventListener("dragover", event => {
      event.preventDefault();
      const target = event.target.closest(".sidebar-section");
      if (!target || target.parentElement !== panelContent || target === dragSrcSection) return;
      panelContent.querySelectorAll(".sidebar-section.drag-over").forEach(el => el.classList.remove("drag-over"));
      target.classList.add("drag-over");
      event.dataTransfer.dropEffect = "move";
    });
    panelContent.addEventListener("drop", event => {
      event.preventDefault();
      const target = event.target.closest(".sidebar-section");
      if (!target || target.parentElement !== panelContent || target === dragSrcSection || !dragSrcSection) return;
      panelContent.insertBefore(dragSrcSection, target);
      panelContent.querySelectorAll(".sidebar-section.drag-over").forEach(el => el.classList.remove("drag-over"));
    });

    // ── Routing controls ─────────────────────────────────────────────────────

    function applyEdgeRoutingChange(patch) {
      applyRoutingPatch(patch);
      saveViewerState();
      rerouteAllVisibleEdgesFromCurrentPositions();
      refreshEdgeOcclusionMasks();
    }

    function applyLayoutRoutingChange(patch) {
      applyRoutingPatch(patch);
      saveViewerState();
      updateVisibilityFull();
    }

    routingCompactnessSelect.addEventListener("change", () => {
      const presetName = routingCompactnessSelect.value;
      applyLayoutRoutingChange({
        compactnessPreset: presetName,
        ...routingPresets[presetName]
      });
    });

    routingGeometrySelect.addEventListener("change", () => {
      applyEdgeRoutingChange({geometry: routingGeometrySelect.value});
    });

    [
      "extraClearance",
      "cornerRadius",
      "parallelSpacing",
      "mergeLaneDistance",
      "polylineBend",
      "splineTension",
      "bezierCurvature",
    ].forEach(key => {
      routingInputs[key].addEventListener("input", () => {
        applyEdgeRoutingChange({ [key]: Number(routingInputs[key].value) });
      });
    });

    ["nodeSpacing", "layerSpacing", "edgeNodeSpacing"].forEach(key => {
      routingInputs[key].addEventListener("input", () => {
        applyLayoutRoutingChange({ [key]: Number(routingInputs[key].value) });
      });
    });

    // ── Other event listeners ────────────────────────────────────────────────

    document.addEventListener("keydown", event => {
      if (quickGuideOwnsFocus()) return;
      const tag = document.activeElement?.tagName?.toLowerCase();
      if (["input", "textarea", "select"].includes(tag) || document.activeElement?.isContentEditable) return;
      if (event.key === "Escape") {
        event.preventDefault();
        deselect();
        return;
      }
      if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        manualPositions.clear();
        svgEl.querySelectorAll(".graph-node").forEach(el => el.removeAttribute("transform"));
        updateVisibilityFull();
        saveViewerState();
        return;
      }
      if (event.key.toLowerCase() === "c") {
        event.preventDefault();
        resetViewState({includeCategories: event.shiftKey});
        return;
      }
      if (event.key.toLowerCase() === "h") {
        event.preventDefault();
        if (event.shiftKey) {
          handleHideUnselected();
        } else {
          handleHideSelected();
        }
        return;
      }
      if (event.key.toLowerCase() === "d") {
        event.preventDefault();
        if (event.shiftKey) {
          handleDimUnselected();
        } else {
          handleDimSelected();
        }
        return;
      }
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        const r = canvasWrapEl.getBoundingClientRect();
        zoomAt(zoomLevel * 1.2, r.left + r.width / 2, r.top + r.height / 2);
        saveViewerState();
        return;
      }
      if (event.key === "-") {
        event.preventDefault();
        const r = canvasWrapEl.getBoundingClientRect();
        zoomAt(zoomLevel / 1.2, r.left + r.width / 2, r.top + r.height / 2);
        saveViewerState();
        return;
      }
      if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        fitGraph();
        saveViewerState();
        return;
      }
    });

    // ── Initialization ────────────────────────────────────────────────────────

    (function applyDocumentTitle() {
      const t = docData.document?.title;
      if (!t) return;
      document.getElementById("panel-title").textContent = t;
    })();

    startBuildRefreshWatcher();
    restoreViewerState();
    syncRoutingControls();
    syncSidebarLayout();
    restoreSidebarOrder();

    window.addEventListener("load", () => {
      updateVisibilityFull();
    });
