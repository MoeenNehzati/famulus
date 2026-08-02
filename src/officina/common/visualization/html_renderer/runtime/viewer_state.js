    // ── State persistence ────────────────────────────────────────────────────

    function saveViewerState() {
      try {
        const payload = {
          version: 5,
          hiddenTypes: Array.from(hiddenTypes),
          hiddenEdgeTypes: Array.from(hiddenEdgeTypes),
          hiddenNodes: Array.from(hiddenNodes),
          dimmedNodes: Array.from(dimmedNodes),
          collapsedContainers: Array.from(collapsedContainers),
          selectedNodeId,
          selectedNodeIds: Array.from(selectedNodeIds),
          selectionSource,
          focusNodeId,
          ancestorFocusMode,
          leftPanelCollapsed,
          rightPanelCollapsed,
          leftPanelWidth,
          rightPanelWidth,
          manualPositions: Array.from(manualPositions.entries()),
          routingConfig,
          filterState: serializeFilterState(),
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
        if (!payload || ![3, 4, 5].includes(payload.version)) throw new Error("unsupported viewer state");
        const arrays = ["hiddenTypes", "hiddenEdgeTypes", "hiddenNodes", "dimmedNodes", "selectedNodeIds", "collapsedContainers", "manualPositions"];
        if (arrays.some(key => payload[key] !== undefined && !Array.isArray(payload[key]))) {
          throw new Error("invalid viewer state collection");
        }
        const containerIds = new Set(parentByNode.values());
        const nextHiddenTypes = new Set((payload.hiddenTypes || []).filter(t => typeStyles[String(t)]).map(String));
        const nextHiddenEdgeTypes = new Set((payload.hiddenEdgeTypes || []).filter(t =>
          presentEdgeTypes.includes(String(t)) || edgeCategoryCatalog.has(String(t))
        ).map(String));
        const nextHiddenNodes = new Set((payload.hiddenNodes || []).filter(id => entityMap.has(String(id))).map(String));
        const nextCollapsed = new Set((payload.collapsedContainers || []).filter(id => containerIds.has(String(id))).map(String));
        hiddenTypes.clear(); nextHiddenTypes.forEach(value => hiddenTypes.add(value));
        hiddenEdgeTypes.clear(); nextHiddenEdgeTypes.forEach(value => hiddenEdgeTypes.add(value));
        hiddenNodes.clear(); nextHiddenNodes.forEach(value => hiddenNodes.add(value));
        dimmedNodes.clear();
        (payload.dimmedNodes || []).filter(id => entityMap.has(String(id))).forEach(id => dimmedNodes.add(String(id)));
        collapsedContainers.clear(); nextCollapsed.forEach(value => collapsedContainers.add(value));
        if (payload.routingConfig) applyRoutingPatch(payload.routingConfig);
        selectedNodeIds.clear();
        const restoredSelection = payload.version === 5
          ? payload.selectedNodeIds || []
          : payload.selectedNodeId ? [payload.selectedNodeId] : [];
        restoredSelection.filter(id => entityMap.has(String(id)) && !hiddenNodes.has(String(id))).forEach(id => selectedNodeIds.add(String(id)));
        selectedNodeId = payload.selectedNodeId && selectedNodeIds.has(String(payload.selectedNodeId))
          ? String(payload.selectedNodeId)
          : Array.from(selectedNodeIds).at(-1) || null;
        selectionSource = payload.selectionSource === "search" ? "search" : "explicit";
        if (payload.focusNodeId && entityMap.has(payload.focusNodeId)) {
          focusNodeId = payload.focusNodeId;
        }
        if (payload.filterState) restoreFilterState(payload.filterState);
        // Support both old boolean and new numeric format
        ancestorFocusMode = typeof payload.ancestorFocusMode === "number"
          ? payload.ancestorFocusMode
          : (payload.ancestorFocusEnabled ? 1 : 0);
        leftPanelCollapsed = payload.version === 4 ? Boolean(payload.leftPanelCollapsed) : false;
        rightPanelCollapsed = payload.version === 4
          ? Boolean(payload.rightPanelCollapsed)
          : Boolean(payload.panelCollapsed);
        if (Number.isFinite(payload.leftPanelWidth)) leftPanelWidth = clampSidebarWidth(payload.leftPanelWidth);
        if (Number.isFinite(payload.rightPanelWidth)) rightPanelWidth = clampSidebarWidth(payload.rightPanelWidth);
        (payload.manualPositions || []).forEach(([id, pos]) => {
          if (entityMap.has(id)) manualPositions.set(id, pos);
        });
        if (typeof payload.panX === "number") {
          panX = payload.panX; panY = payload.panY; zoomLevel = payload.zoomLevel || 1;
          applyTransform();
          hasFittedOnce = true;
        }
      } catch (e) {
        window.localStorage.removeItem(viewerStateKey);
      }
    }

    function saveSidebarOrder() {
      try {
        const order = Array.from(panelContent.querySelectorAll(":scope > .sidebar-section")).map(el => el.dataset.sectionId);
        window.localStorage.setItem(viewerStateKey + "::sidebar", JSON.stringify(order));
      } catch (e) {}
    }

    function restoreSidebarOrder() {
      try {
        const raw = window.localStorage.getItem(viewerStateKey + "::sidebar");
        if (!raw) return;
        const order = JSON.parse(raw);
        order.forEach(sectionId => {
          const el = Array.from(panelContent.children).find(child => child.dataset.sectionId === sectionId);
          if (el) panelContent.appendChild(el);
        });
      } catch (e) {}
    }

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
