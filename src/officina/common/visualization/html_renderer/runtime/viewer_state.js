    // ── State persistence ────────────────────────────────────────────────────

    function saveViewerState() {
      try {
        const payload = {
          hiddenTypes: Array.from(hiddenTypes),
          hiddenEdgeTypes: Array.from(hiddenEdgeTypes),
          hiddenNodes: Array.from(hiddenNodes),
          collapsedContainers: Array.from(collapsedContainers),
          selectedNodeId,
          focusNodeId,
          ancestorFocusMode,
          panelCollapsed: layoutEl.classList.contains("panel-collapsed"),
          manualPositions: Array.from(manualPositions.entries()),
          routingConfig,
          panX, panY, zoomLevel
        };
        window.localStorage.setItem(viewerStateKey, JSON.stringify(payload));
      } catch (e) {}
    }

    function restoreViewerState() {
      try {
        const raw = window.localStorage.getItem(viewerStateKey);
        if (!raw) return;
        const payload = JSON.parse(raw);
        (payload.hiddenTypes || []).forEach(t => { if (typeStyles[t]) hiddenTypes.add(t); });
        (payload.hiddenEdgeTypes || []).forEach(t => { if (presentEdgeTypes.includes(String(t))) hiddenEdgeTypes.add(String(t)); });
        (payload.hiddenNodes || []).forEach(id => { if (entityMap.has(id)) hiddenNodes.add(id); });
        (payload.collapsedContainers || []).forEach(id => { if (entityMap.has(id)) collapsedContainers.add(id); });
        if (payload.routingConfig) applyRoutingPatch(payload.routingConfig);
        if (payload.selectedNodeId && entityMap.has(payload.selectedNodeId)) {
          selectedNodeId = payload.selectedNodeId;
        }
        if (payload.focusNodeId && entityMap.has(payload.focusNodeId)) {
          focusNodeId = payload.focusNodeId;
        }
        // Support both old boolean and new numeric format
        ancestorFocusMode = typeof payload.ancestorFocusMode === "number"
          ? payload.ancestorFocusMode
          : (payload.ancestorFocusEnabled ? 1 : 0);
        if (payload.panelCollapsed) layoutEl.classList.add("panel-collapsed");
        (payload.manualPositions || []).forEach(([id, pos]) => {
          if (entityMap.has(id)) manualPositions.set(id, pos);
        });
        if (typeof payload.panX === "number") {
          panX = payload.panX; panY = payload.panY; zoomLevel = payload.zoomLevel || 1;
          applyTransform();
          hasFittedOnce = true;
        }
      } catch (e) {}
    }

    function saveSidebarOrder() {
      try {
        const order = Array.from(panelContent.querySelectorAll(".sidebar-section")).map(el => el.dataset.sectionId);
        window.localStorage.setItem(viewerStateKey + "::sidebar", JSON.stringify(order));
      } catch (e) {}
    }

    function restoreSidebarOrder() {
      try {
        const raw = window.localStorage.getItem(viewerStateKey + "::sidebar");
        if (!raw) return;
        const order = JSON.parse(raw);
        order.forEach(sectionId => {
          const el = panelContent.querySelector(`[data-section-id="${sectionId}"]`);
          if (el) panelContent.appendChild(el);
        });
      } catch (e) {}
    }

    // ── Panel toggle ─────────────────────────────────────────────────────────

    function syncPanelToggle() {
      const collapsed = layoutEl.classList.contains("panel-collapsed");
      panelToggle.textContent = collapsed ? "⟨" : "⟩";
      panelToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      panelToggle.setAttribute("title", collapsed ? "Expand side panel" : "Collapse side panel");
    }
    panelToggle.addEventListener("click", () => {
      layoutEl.classList.toggle("panel-collapsed");
      syncPanelToggle();
      saveViewerState();
    });

    // ── Pan / zoom ────────────────────────────────────────────────────────────

    function applyTransform() {
      svgEl.style.transformOrigin = "0 0";
      svgEl.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomLevel})`;
    }

    function fitGraph() {
      const canvasRect = canvasWrapEl.getBoundingClientRect();
      const svgW = parseFloat(svgEl.getAttribute("width")) || 1200;
      const svgH = parseFloat(svgEl.getAttribute("height")) || 800;
      const padding = 40;
      const availW = Math.max(1, canvasRect.width - padding * 2);
      const availH = Math.max(1, canvasRect.height - padding * 2);
      zoomLevel = Math.min(availW / svgW, availH / svgH, 1);
      panX = (canvasRect.width - svgW * zoomLevel) / 2;
      panY = padding;
      applyTransform();
    }

    function zoomAt(newZoom, clientX, clientY) {
      newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, newZoom));
      const canvasRect = canvasWrapEl.getBoundingClientRect();
      const cx = clientX - canvasRect.left;
      const cy = clientY - canvasRect.top;
      const svgX = (cx - panX) / zoomLevel;
      const svgY = (cy - panY) / zoomLevel;
      zoomLevel = newZoom;
      panX = cx - svgX * zoomLevel;
      panY = cy - svgY * zoomLevel;
      applyTransform();
    }

    // Wheel zoom
    canvasWrapEl.addEventListener("wheel", event => {
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      zoomAt(zoomLevel * factor, event.clientX, event.clientY);
      saveViewerState();
    }, { passive: false });

    // Pan: mousedown on empty canvas space
    canvasWrapEl.addEventListener("mousedown", event => {
      if (event.button !== 0) return;
      if (draggingNodeId !== null) return;
      const target = event.target;
      if (target.closest && target.closest(".graph-node")) return;
      if (target.dataset && target.dataset.edgeId) return;
      if (target.classList && (target.classList.contains("edge-arrow") || target.classList.contains("edge-path"))) return;
      isPanning = true;
      panStartClientX = event.clientX;
      panStartClientY = event.clientY;
      panStartX = panX;
      panStartY = panY;
      canvasWrapEl.classList.add("panning");
      event.preventDefault();
    });

