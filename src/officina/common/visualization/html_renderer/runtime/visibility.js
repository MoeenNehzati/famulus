    // ── Visibility helpers ───────────────────────────────────────────────────

    function isHiddenNode(nodeId) {
      if (nodeHiddenByDetailLevel(nodeId)) return true;
      if (isNodeFilteredOut(nodeId)) return true;
      const category = nodeCategories.get(nodeId);
      if ((!nodeIsRetainedContext(nodeId) && hiddenTypes.has(category)) || hiddenNodes.has(nodeId)) return true;
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

      const hasFocusHidden = ancestorHiddenByFocus.size > 0;
      const hasHidden = hiddenEntities.length > 0;

      if (!hasFocusHidden && !hasHidden) { hiddenNodesEl.textContent = "None"; return; }

      // Ancestor-focus group (mode 2): one collapsed item for all focus-hidden nodes
      if (hasFocusHidden) {
        const count = ancestorHiddenByFocus.size;
        const focusEntity = focusNodeId ? entityMap.get(focusNodeId) : null;
        const focusLabel = focusEntity ? focusEntity.short_title : "selection";
        const groupItem = document.createElement("div");
        groupItem.className = "hidden-node-item";
        groupItem.tabIndex = 0;
        groupItem.setAttribute("role", "button");
        groupItem.innerHTML = `
          <div><strong>Non-ancestors of ${escapeHtml(focusLabel)}</strong></div>
          <div class="hidden-node-item-meta">${count} node${count !== 1 ? "s" : ""} hidden — click to restore all</div>
        `;
        groupItem.addEventListener("click", () => {
          ancestorFocusMode = 0;
          focusNodeId = null;
          saveViewerState();
          applyAncestorFocus();
        });
        groupItem.addEventListener("keydown", event => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          groupItem.click();
        });
        hiddenNodesEl.appendChild(groupItem);
      }

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
          hiddenNodes.delete(entity.id);
          saveViewerState();
          updateVisibilityFast();
          rerouteIncidentEdgesFromCurrentPositions(entity.id);
        });
        item.addEventListener("click", () => {
          showEntityDetails(entity);
        });
        item.addEventListener("keydown", event => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          hiddenNodes.delete(entity.id);
          saveViewerState();
          updateVisibilityFast();
          rerouteIncidentEdgesFromCurrentPositions(entity.id);
        });
        hiddenNodesEl.appendChild(item);
      });
      typesetElement(hiddenNodesEl);
    }

    // ── Ancestor focus ───────────────────────────────────────────────────────

    function collectAncestors(nodeId) {
      const keep = new Set();
      const stack = [nodeId];
      while (stack.length) {
        const current = stack.pop();
        if (!current || keep.has(current)) continue;
        keep.add(current);
        for (const edge of incoming.get(current) || []) {
          const edgeType = String(edge.type || "unknown");
          const relationEligible = !edgeCategorySetContains(edgeType, hiddenEdgeTypes) &&
            !edgeCategorySetContains(edgeType, filterState.excludedEdgeTypes);
          if (relationEligible) stack.push(edge.source);
        }
      }
      Array.from(keep).forEach(id => {
        let parent = parentByNode.get(id);
        while (parent && !keep.has(parent)) {
          keep.add(parent);
          parent = parentByNode.get(parent);
        }
      });
      return keep;
    }

    function syncToolbar() {
      const selectedVisible = visibleSelectedNodeIds();
      const hasSelection = selectedVisible.length > 0;
      const hasFocus = !!focusNodeId && !isHiddenNode(focusNodeId);
      // Focus toggle
      focusToggle.disabled = !hasSelection && !hasFocus;
      if ((!hasSelection && !hasFocus) || ancestorFocusMode === 0) {
        focusToggle.textContent = "Highlight ancestors";
        focusToggle.classList.remove("active");
      } else if (ancestorFocusMode === 1) {
        focusToggle.textContent = "Hide non-ancestors";
        focusToggle.classList.add("active");
      } else {
        focusToggle.textContent = "Show full graph";
        focusToggle.classList.add("active");
      }
      hideSelectedBtn.disabled = !hasSelection;
      hideSelectedBtn.textContent = hasSelection ? `Hide selected (${selectedVisible.length})` : "Hide selected";
      dimSelectedBtn.disabled = !hasSelection;
      const allDimmed = hasSelection && selectedVisible.every(nodeId => dimmedNodes.has(nodeId));
      dimSelectedBtn.textContent = allDimmed ? `Undim selected (${selectedVisible.length})` : `Dim selected${hasSelection ? ` (${selectedVisible.length})` : ""}`;
    }

    // Keep syncFocusToggle as an alias so existing callsites still work
    function syncFocusToggle() { syncToolbar(); }

    function applyAncestorFocus() {
      syncFocusToggle();
      ancestorHiddenByFocus.clear();
      const active = ancestorFocusMode > 0 && focusNodeId && !isHiddenNode(focusNodeId);
      const keep = active ? collectAncestors(focusNodeId) : null;

      document.querySelectorAll(".graph-node").forEach(nodeEl => {
        const nodeId = nodeEl.dataset.nodeId;
        if (active && !keep.has(nodeId)) {
          if (ancestorFocusMode === 1) {
            nodeEl.style.opacity = "0.18";
            nodeEl.style.display = "";
          } else {
            nodeEl.style.display = "none";
            ancestorHiddenByFocus.add(nodeId);
          }
        } else {
          nodeEl.style.opacity = "1";
          nodeEl.style.display = isHiddenNode(nodeId) ? "none" : "";
        }
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
        } else if (active && ancestorFocusMode === 2 && (ancestorHiddenByFocus.has(src) || ancestorHiddenByFocus.has(dst))) {
          pathEl.style.display = "none";
          pathEl.style.opacity = "";
        } else if (active && ancestorFocusMode === 1 && (!keep.has(src) || !keep.has(dst))) {
          pathEl.style.opacity = "0.08";
          pathEl.style.display = "";
        } else {
          pathEl.style.opacity = "0.96";
          pathEl.style.display = "";
        }
        syncArrowheadForPath(pathEl);
      });

      renderHiddenNodes();
      applyFilterPresentation();
    }
