    // ── Visibility helpers ───────────────────────────────────────────────────

    function nodeOmissionCause(nodeId) {
      if (hiddenNodes.has(nodeId)) return "user-hidden";
      let current = parentByNode.get(nodeId);
      const seen = new Set();
      while (current && !seen.has(current)) {
        if (hiddenNodes.has(current)) return "user-hidden";
        seen.add(current);
        current = parentByNode.get(current);
      }
      if (isNodeFilteredOut(nodeId)) return "filter-hidden";
      const category = nodeCategories.get(nodeId);
      if (!nodeIsRetainedContext(nodeId) && hiddenTypes.has(category)) return "filter-hidden";
      if (nodeHiddenByDetailLevel(nodeId)) return "detail-hidden";
      return null;
    }

    function isHiddenNode(nodeId) {
      if (nodeOmissionCause(nodeId)) return true;
      let current = parentByNode.get(nodeId);
      const seen = new Set();
      while (current && !seen.has(current)) {
        if (collapsedContainers.has(current)) return true;
        seen.add(current);
        current = parentByNode.get(current);
      }
      return false;
    }

    // ── Hidden nodes list ────────────────────────────────────────────────────

    function renderHiddenNodes() {
      const hiddenEntities = docData.entities
        .filter(e => hiddenNodes.has(e.id))
        .sort((a, b) => (a.position || 0) - (b.position || 0) || a.short_title.localeCompare(b.short_title));
      clearMathBeforeMutation(hiddenNodesEl);
      hiddenNodesEl.innerHTML = "";

      const hasHidden = hiddenEntities.length > 0;

      if (!hasHidden) { hiddenNodesEl.textContent = "None"; return; }

      // Individually double-click-hidden nodes
      hiddenEntities.forEach(entity => {
        const item = document.createElement("div");
        item.className = "hidden-node-item";
        item.tabIndex = 0;
        item.setAttribute("role", "button");
        item.innerHTML = `
          <div><strong>${escapeHtml(entity.short_title)}</strong></div>
          <div class="hidden-node-item-meta">${escapeHtml(entity.ref || "")}</div>
        `;
        item.addEventListener("dblclick", () => {
          showNodes([entity.id]);
        });
        item.addEventListener("click", () => {
          showEntityDetails(entity);
        });
        item.addEventListener("keydown", event => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          showNodes([entity.id]);
        });
        hiddenNodesEl.appendChild(item);
      });
      typesetElement(hiddenNodesEl);
    }

    function syncToolbar() {
      const selectedVisible = visibleSelectedNodeIds();
      const hasSelection = selectedVisible.length > 0;
      hideSelectedBtn.disabled = !hasSelection;
      hideSelectedBtn.textContent = hasSelection ? `Hide selected (${selectedVisible.length})` : "Hide selected";
      dimSelectedBtn.disabled = !hasSelection;
      const allDimmed = hasSelection && selectedVisible.every(nodeId => dimmedNodes.has(nodeId));
      dimSelectedBtn.textContent = allDimmed ? `Undim selected (${selectedVisible.length})` : `Dim selected${hasSelection ? ` (${selectedVisible.length})` : ""}`;
      const hideUnselected = hasSelection
        ? visibleUnselectedNodeIds({preserveSelectionAncestors: true})
        : [];
      hideUnselectedBtn.disabled = hideUnselected.length === 0;
      hideUnselectedBtn.textContent = hideUnselected.length
        ? `Hide unselected (${hideUnselected.length})`
        : "Hide unselected";
      const dimUnselected = hasSelection ? visibleUnselectedNodeIds() : [];
      dimUnselectedBtn.disabled = dimUnselected.length === 0;
      const allUnselectedDimmed = dimUnselected.length > 0 && dimUnselected.every(nodeId => dimmedNodes.has(nodeId));
      dimUnselectedBtn.textContent = allUnselectedDimmed
        ? `Undim unselected (${dimUnselected.length})`
        : `Dim unselected${dimUnselected.length ? ` (${dimUnselected.length})` : ""}`;
    }

    function applyVisibilityPresentation() {
      syncToolbar();
      document.querySelectorAll(".graph-node").forEach(nodeEl => {
        const nodeId = nodeEl.dataset.nodeId;
        nodeEl.style.opacity = "1";
        nodeEl.style.display = isHiddenNode(nodeId) ? "none" : "";
      });

      document.querySelectorAll(".edge-path").forEach(pathEl => {
        const src = pathEl.dataset.sourceNodeId;
        const dst = pathEl.dataset.targetNodeId;
        const edge = pathEl.__edgeMeta || {
          source: src,
          target: dst,
          type: pathEl.dataset.edgeType,
        };
        const edgeTypeHidden = edgeSuppressedByCategorySet(edge, hiddenEdgeTypes);
        const endpointHidden = isHiddenNode(src) || isHiddenNode(dst);
        if (edgeTypeHidden || endpointHidden) {
          pathEl.style.display = "none";
          pathEl.style.opacity = "";
        } else {
          pathEl.style.opacity = "0.96";
          pathEl.style.display = "";
        }
        syncArrowheadForPath(pathEl);
      });

      renderHiddenNodes();
      applyFilterPresentation();
    }
