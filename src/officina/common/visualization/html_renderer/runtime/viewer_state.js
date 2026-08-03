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
        hiddenTypes.clear();
        hiddenEdgeTypes.clear();
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
        if (payload.filterState) restoreFilterState(payload.filterState);
        nextHiddenTypes.forEach(value => filterState.excludedCategories.add(value));
        nextHiddenEdgeTypes.forEach(value => filterState.excludedEdgeTypes.add(value));
        refreshFilterControls();
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
        const order = Array.from(panelContent.querySelectorAll(":scope > .sidebar-section:not(.fixed-top-sidebar-section)"))
          .map(el => el.dataset.sectionId);
        window.localStorage.setItem(viewerStateKey + "::sidebar", JSON.stringify(order));
      } catch (e) {}
    }

    function restoreSidebarOrder() {
      try {
        const raw = window.localStorage.getItem(viewerStateKey + "::sidebar");
        if (!raw) return;
        const order = JSON.parse(raw);
        if (!Array.isArray(order)) {
          window.localStorage.removeItem(viewerStateKey + "::sidebar");
          return;
        }
        order.forEach(sectionId => {
          const el = Array.from(panelContent.children).find(child => child.dataset.sectionId === sectionId);
          if (el && !el.classList.contains("fixed-top-sidebar-section")) panelContent.appendChild(el);
        });
      } catch (e) {}
    }

    // ── Pan / zoom ────────────────────────────────────────────────────────────

    function applyTransform() {
      svgEl.style.transformOrigin = "0 0";
      svgEl.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomLevel})`;
    }

    function visibleContentBounds() {
      const positions = docData.entities
        .filter(entity => !isHiddenNode(entity.id))
        .map(entity => getEffectivePos(entity.id))
        .filter(Boolean);
      if (!positions.length) return null;
      const left = Math.min(...positions.map(position => position.x));
      const top = Math.min(...positions.map(position => position.y));
      const right = Math.max(...positions.map(position => position.x + position.width));
      const bottom = Math.max(...positions.map(position => position.y + position.height));
      return {x: left, y: top, width: right - left, height: bottom - top};
    }

    function fitGraph() {
      const canvasRect = canvasWrapEl.getBoundingClientRect();
      const bounds = visibleContentBounds();
      if (!bounds) return;
      const padding = 40;
      const availW = Math.max(1, canvasRect.width - padding * 2);
      const availH = Math.max(1, canvasRect.height - padding * 2);
      zoomLevel = Math.max(MIN_ZOOM, Math.min(availW / Math.max(1, bounds.width), availH / Math.max(1, bounds.height), 1));
      panX = padding + (availW - bounds.width * zoomLevel) / 2 - bounds.x * zoomLevel;
      panY = padding + (availH - bounds.height * zoomLevel) / 2 - bounds.y * zoomLevel;
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

    function zoomTowardContent(newZoom) {
      const selected = selectedNodeId && !isHiddenNode(selectedNodeId)
        ? getEffectivePos(selectedNodeId)
        : null;
      const bounds = visibleContentBounds();
      const target = selected
        ? {x: selected.x + selected.width / 2, y: selected.y + selected.height / 2}
        : bounds
          ? {x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2}
          : null;
      if (!target) return;
      const canvasRect = canvasWrapEl.getBoundingClientRect();
      zoomLevel = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, newZoom));
      panX = canvasRect.width / 2 - target.x * zoomLevel;
      panY = canvasRect.height / 2 - target.y * zoomLevel;
      applyTransform();
    }

    // Wheel zoom
    canvasWrapEl.addEventListener("wheel", event => {
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      zoomAt(zoomLevel * factor, event.clientX, event.clientY);
      saveViewerState();
    }, { passive: false });

    let touchGesture = null;
    let lastCanvasTap = null;
    const touchMidpoint = touches => ({
      x: (touches[0].clientX + touches[1].clientX) / 2,
      y: (touches[0].clientY + touches[1].clientY) / 2
    });
    const touchDistance = touches => Math.hypot(
      touches[0].clientX - touches[1].clientX,
      touches[0].clientY - touches[1].clientY
    );

    canvasWrapEl.addEventListener("touchstart", event => {
      if (event.touches.length === 2) {
        event.preventDefault();
        touchGesture = {
          mode: "pinch",
          startedAt: Date.now(),
          lastDistance: touchDistance(event.touches),
          midpoint: touchMidpoint(event.touches),
          moved: false
        };
        return;
      }
      if (event.touches.length !== 1) return;
      const target = event.target;
      if (target.closest?.(".graph-node") || target.dataset?.edgeId) return;
      event.preventDefault();
      const touch = event.touches[0];
      touchGesture = {
        mode: "pan",
        startedAt: Date.now(),
        startX: touch.clientX,
        startY: touch.clientY,
        panX,
        panY,
        moved: false
      };
    }, {passive: false});

    canvasWrapEl.addEventListener("touchmove", event => {
      if (!touchGesture) return;
      event.preventDefault();
      if (touchGesture.mode === "pinch" && event.touches.length >= 2) {
        const distance = touchDistance(event.touches);
        const midpoint = touchMidpoint(event.touches);
        const factor = touchGesture.lastDistance > 0 ? distance / touchGesture.lastDistance : 1;
        if (Math.abs(distance - touchGesture.lastDistance) > 3 || Math.hypot(midpoint.x - touchGesture.midpoint.x, midpoint.y - touchGesture.midpoint.y) > 3) {
          touchGesture.moved = true;
        }
        zoomAt(zoomLevel * factor, midpoint.x, midpoint.y);
        touchGesture.lastDistance = distance;
        touchGesture.midpoint = midpoint;
        return;
      }
      if (touchGesture.mode === "pan" && event.touches.length === 1) {
        const touch = event.touches[0];
        const dx = touch.clientX - touchGesture.startX;
        const dy = touch.clientY - touchGesture.startY;
        if (Math.hypot(dx, dy) > 5) touchGesture.moved = true;
        panX = touchGesture.panX + dx;
        panY = touchGesture.panY + dy;
        applyTransform();
      }
    }, {passive: false});

    canvasWrapEl.addEventListener("touchend", event => {
      if (!touchGesture || event.touches.length > 0) return;
      event.preventDefault();
      const completed = touchGesture;
      touchGesture = null;
      if (completed.mode === "pinch") {
        if (!completed.moved && Date.now() - completed.startedAt < 300) {
          zoomAt(zoomLevel / 1.3, completed.midpoint.x, completed.midpoint.y);
        }
        saveViewerState();
        return;
      }
      const changed = event.changedTouches[0];
      if (!changed) return;
      if (!completed.moved) {
        const tap = {x: changed.clientX, y: changed.clientY, at: Date.now()};
        if (lastCanvasTap && tap.at - lastCanvasTap.at < 320 && Math.hypot(tap.x - lastCanvasTap.x, tap.y - lastCanvasTap.y) < 30) {
          zoomAt(zoomLevel * 1.3, tap.x, tap.y);
          lastCanvasTap = null;
        } else {
          lastCanvasTap = tap;
        }
      }
      saveViewerState();
    }, {passive: false});

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
