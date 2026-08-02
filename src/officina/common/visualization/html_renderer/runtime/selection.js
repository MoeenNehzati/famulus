    // ── Unified explicit/search node selection and user dimming ──────────────

    function visibleSelectedNodeIds() {
      return Array.from(selectedNodeIds).filter(id => entityMap.has(id) && !isHiddenNode(id));
    }

    function syncSelectionPresentation() {
      svgEl.querySelectorAll(".graph-node").forEach(nodeEl => {
        const nodeId = nodeEl.dataset.nodeId;
        nodeEl.classList.toggle("selected", selectedNodeIds.has(nodeId));
        nodeEl.classList.toggle("primary-selected", nodeId === selectedNodeId);
        nodeEl.classList.toggle("user-dimmed", dimmedNodes.has(nodeId));
        nodeEl.setAttribute("aria-selected", selectedNodeIds.has(nodeId) ? "true" : "false");
      });
      syncToolbar();
    }

    function showSelectionDetails() {
      if (!selectedNodeId || !entityMap.has(selectedNodeId)) {
        clearMathBeforeMutation(details);
        details.innerHTML = "Select a node or edge to inspect its metadata.";
        rawJsonCodeEl.textContent = JSON.stringify(docData, null, 2);
        return;
      }
      showEntityDetails(entityMap.get(selectedNodeId));
      if (selectedNodeIds.size <= 1) return;
      const selected = Array.from(selectedNodeIds)
        .map(id => entityMap.get(id))
        .filter(Boolean);
      const visibleLabels = selected.slice(0, 12)
        .map(entity => `<code>${escapeHtml(entity.short_title || entity.id)}</code>`)
        .join("");
      const remainder = selected.length > 12 ? `<span>+${selected.length - 12} more</span>` : "";
      details.insertAdjacentHTML("afterbegin", `
        <section class="selection-summary" aria-label="Selected nodes">
          <strong>${selected.length} nodes selected</strong>
          <div class="selection-summary-items">${visibleLabels}${remainder}</div>
          <div class="small">Details below are for the primary node.</div>
        </section>`);
    }

    function setNodeSelection(ids, primaryId = null, source = "explicit", {persist = true} = {}) {
      const normalized = Array.from(new Set(ids))
        .map(String)
        .filter(id => entityMap.has(id) && !hiddenNodes.has(id));
      selectedNodeIds.clear();
      normalized.forEach(id => selectedNodeIds.add(id));
      selectedNodeId = primaryId && selectedNodeIds.has(String(primaryId))
        ? String(primaryId)
        : normalized[normalized.length - 1] || null;
      selectionSource = source === "search" ? "search" : "explicit";
      rebuildRetainedOwners();
      syncSelectionPresentation();
      showSelectionDetails();
      applyAncestorFocus();
      if (persist) saveViewerState();
    }

    function toggleNodeSelection(nodeId) {
      const next = new Set(selectedNodeIds);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      setNodeSelection(next, next.has(nodeId) ? nodeId : Array.from(next).at(-1), "explicit");
    }

    function removeNodesFromSelection(nodeIds, {persist = false} = {}) {
      const removed = new Set(Array.from(nodeIds).map(String));
      const remaining = Array.from(selectedNodeIds).filter(id => !removed.has(id));
      const nextPrimary = removed.has(String(selectedNodeId)) ? remaining.at(-1) || null : selectedNodeId;
      setNodeSelection(remaining, nextPrimary, "explicit", {persist});
    }

    function searchSelectionIds() {
      const query = normalizedFilterText(filterState.query);
      if (!query) return [];
      const matches = new Set();
      docData.entities.forEach(entity => {
        if (!hiddenNodes.has(entity.id) && nodeMatchesSearch(entity)) matches.add(entity.id);
      });
      edgeData.forEach(edge => {
        if (!edgeMatchesSearch(edge)) return;
        if (!hiddenNodes.has(edge.source)) matches.add(edge.source);
        if (!hiddenNodes.has(edge.target)) matches.add(edge.target);
      });
      return Array.from(matches);
    }

    function syncSearchSelection({persist = false} = {}) {
      const queryActive = Boolean(normalizedFilterText(filterState.query));
      if (queryActive) {
        const matches = searchSelectionIds();
        setNodeSelection(matches, matches[0] || null, "search", {persist});
      } else if (selectionSource === "search") {
        setNodeSelection([], null, "explicit", {persist});
      }
    }
