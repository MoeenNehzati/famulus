    // Domain-neutral search, facet filtering, history, and stable projection.
    const categoryCatalog = new Map(
      (docData.categories || []).map(category => [String(category.id), category])
    );
    const categoryChildren = new Map();
    categoryCatalog.forEach((category, categoryId) => {
      if (!category.parent || !categoryCatalog.has(String(category.parent))) return;
      const parentId = String(category.parent);
      if (!categoryChildren.has(parentId)) categoryChildren.set(parentId, []);
      categoryChildren.get(parentId).push(categoryId);
    });

    const filterState = {
      query: "",
      detailLevel: detailLevelRank.has(String(initialVisibility.detail_level))
        ? String(initialVisibility.detail_level)
        : defaultDetailLevel,
      excludedTypes: new Set(),
      excludedKinds: new Set(),
      excludedCategories: new Set(),
      excludedEdgeTypes: new Set(),
    };
    hiddenTypes.forEach(value => filterState.excludedCategories.add(value));
    hiddenEdgeTypes.forEach(value => filterState.excludedEdgeTypes.add(value));
    hiddenTypes.clear();
    hiddenEdgeTypes.clear();
    const filterUndoStack = [];
    const filterRedoStack = [];
    let retainedOwnerIds = new Set();
    let retainedEndpointIds = new Set();
    let searchEditStartSnapshot = null;

    function normalizedFilterText(value) {
      return String(value || "").trim().toLocaleLowerCase();
    }

    function nodeSearchText(entity) {
      return normalizedFilterText([
        entity.id, entity.short_title, entity.label, entity.title,
        entity.description, entity.type, entity.kind, entity.category,
        ...(Array.isArray(entity.tags) ? entity.tags : []),
      ].filter(Boolean).join(" "));
    }

    function edgeSearchText(edge) {
      const constituents = edgeConstituents(edge);
      return normalizedFilterText([
        edge.type, edge.label, edge.edge_label,
        edge.description, edge.details?.summary,
        ...constituents.flatMap(constituent => [
          constituent.type, constituent.label, constituent.edge_label,
          constituent.description, constituent.details?.summary,
          constituent.metadata?.outside_id,
        ]),
      ].filter(Boolean).join(" "));
    }

    function nodeMatchesSearch(entity) {
      const query = normalizedFilterText(filterState.query);
      return !query || nodeSearchText(entity).includes(query);
    }

    function edgeMatchesSearch(edge) {
      const query = normalizedFilterText(filterState.query);
      return !query || edgeSearchText(edge).includes(query);
    }

    function categoryIsExcluded(categoryId) {
      let current = String(categoryId || "");
      const seen = new Set();
      while (current && !seen.has(current)) {
        if (filterState.excludedCategories.has(current)) return true;
        seen.add(current);
        current = String(categoryCatalog.get(current)?.parent || "");
      }
      return false;
    }

    function nodeFailsFilter(entity) {
      if (!entity) return true;
      if (categoryIsExcluded(entity.category || entity.type || "unknown")) return true;
      return false;
    }

    function nodeHiddenByDetailLevel(nodeId) {
      if (!filterState.detailLevel || !detailLevelRank.size) return false;
      const entity = entityMap.get(nodeId);
      const entityRank = detailLevelRank.get(String(entity?.detail_level || ""));
      const activeRank = detailLevelRank.get(filterState.detailLevel);
      return entityRank !== undefined && activeRank !== undefined && entityRank > activeRank;
    }

    function edgeFailsFilter(edge) {
      return edgeSuppressedByCategorySet(edge, filterState.excludedEdgeTypes);
    }

    function rebuildRetainedOwners() {
      retainedOwnerIds = new Set();
      retainedEndpointIds = new Set();
      docData.entities.forEach(entity => {
        const retainedByInteraction = selectedNodeIds.has(entity.id) || retainedEndpointIds.has(entity.id);
        if (nodeHiddenByDetailLevel(entity.id) && !retainedByInteraction) return;
        if (nodeFailsFilter(entity) && !retainedByInteraction) return;
        let current = parentByNode.get(entity.id);
        const seen = new Set();
        while (current && !seen.has(current)) {
          seen.add(current);
          retainedOwnerIds.add(current);
          current = parentByNode.get(current);
        }
      });
    }

    function isNodeFilteredOut(nodeId) {
      if (nodeIsRetainedContext(nodeId)) return false;
      return nodeFailsFilter(entityMap.get(nodeId));
    }

    function nodeIsRetainedContext(nodeId) {
      return selectedNodeIds.has(nodeId) || retainedOwnerIds.has(nodeId) || retainedEndpointIds.has(nodeId);
    }

    function isEdgeFilteredOut(edge) {
      return edgeFailsFilter(edge);
    }

    function serializeFilterState() {
      return {
        query: filterState.query,
        detailLevel: filterState.detailLevel,
        excludedTypes: Array.from(filterState.excludedTypes),
        excludedKinds: Array.from(filterState.excludedKinds),
        excludedCategories: Array.from(filterState.excludedCategories),
        excludedEdgeTypes: Array.from(filterState.excludedEdgeTypes),
      };
    }

    function restoreFilterState(payload) {
      filterState.query = typeof payload.query === "string" ? payload.query : "";
      filterState.detailLevel = detailLevelRank.has(String(payload.detailLevel))
        ? String(payload.detailLevel)
        : defaultDetailLevel;
      filterState.excludedTypes.clear();
      filterState.excludedKinds.clear();
      ["excludedCategories", "excludedEdgeTypes"].forEach(key => {
        filterState[key] = new Set(Array.isArray(payload[key]) ? payload[key].map(String) : []);
      });
      rebuildRetainedOwners();
      refreshFilterControls();
    }

    function filterSnapshot() {
      return JSON.stringify(serializeFilterState());
    }

    function restoreFilterSnapshot(snapshot) {
      const previousDetailLevel = filterState.detailLevel;
      searchEditStartSnapshot = null;
      restoreFilterState(JSON.parse(snapshot));
      syncSearchSelection({persist: false});
      if (hasFullLayout && previousDetailLevel !== filterState.detailLevel) updateVisibilityFull();
      else applyFilterProjection();
      saveViewerState();
    }

    function commitSearchEdit() {
      const after = filterSnapshot();
      if (searchEditStartSnapshot && searchEditStartSnapshot !== after) {
        filterUndoStack.push(searchEditStartSnapshot);
        filterRedoStack.length = 0;
      }
      searchEditStartSnapshot = null;
    }

    function mutateFilter(mutator) {
      commitSearchEdit();
      const graphBefore = graphStateSnapshot();
      const before = filterSnapshot();
      const previousDetailLevel = filterState.detailLevel;
      mutator();
      syncSearchSelection({persist: false});
      const after = filterSnapshot();
      const graphChanged = JSON.stringify(graphBefore) !== JSON.stringify(graphStateSnapshot());
      if (before === after && !graphChanged) return;
      if (before !== after) {
        filterUndoStack.push(before);
        filterRedoStack.length = 0;
      }
      rebuildRetainedOwners();
      refreshFilterControls();
      if (hasFullLayout && previousDetailLevel !== filterState.detailLevel) {
        const hiddenSelected = Array.from(selectedNodeIds).filter(nodeHiddenByDetailLevel);
        if (hiddenSelected.length) removeNodesFromSelection(hiddenSelected);
        updateVisibilityFull();
      } else {
        applyFilterProjection();
      }
      syncSelectionPresentation();
      showSelectionDetails();
      saveViewerState();
      recordGraphHistory(graphBefore, graphStateSnapshot());
    }

    function applyFilterPresentation() {
      const queryActive = Boolean(normalizedFilterText(filterState.query));
      docData.entities.forEach(entity => {
        const nodeEl = nodeElement(entity.id);
        if (!nodeEl) return;
        const fails = nodeFailsFilter(entity);
        const retained = retainedOwnerIds.has(entity.id) || retainedEndpointIds.has(entity.id) || selectedNodeIds.has(entity.id);
        nodeEl.classList.remove("filter-dimmed", "filter-match");
        nodeEl.classList.toggle("filter-retained-owner", retainedOwnerIds.has(entity.id) && fails);
        nodeEl.classList.toggle("filter-retained-endpoint", retainedEndpointIds.has(entity.id) && fails);
        nodeEl.dataset.filterDisposition = fails
          ? (selectedNodeIds.has(entity.id) ? "retained-selection" : retainedOwnerIds.has(entity.id) ? "retained-owner" : retainedEndpointIds.has(entity.id) ? "retained-endpoint" : "hidden")
          : queryActive && nodeMatchesSearch(entity) ? "matched" : "eligible";
      });
      edgeLayer.querySelectorAll(".edge-path").forEach(path => {
        const edge = path.__edgeMeta || edgeById.get(String(path.dataset.edgeId)) || {
          source: path.dataset.sourceNodeId,
          target: path.dataset.targetNodeId,
          type: path.dataset.edgeType,
        };
        const endpointFails = nodeFailsFilter(entityMap.get(edge.source)) || nodeFailsFilter(entityMap.get(edge.target));
        const matchingRelation = edgeMatchesSearch(edge);
        const fails = edgeFailsFilter(edge) || (endpointFails && !matchingRelation);
        path.classList.remove("filter-dimmed");
        path.classList.toggle("filter-match", queryActive && edgeMatchesSearch(edge));
        if (fails) path.style.display = "none";
        const arrow = arrowForPath(path);
        if (arrow) {
          arrow.classList.remove("filter-dimmed");
          if (fails) arrow.style.display = "none";
        }
      });
      updateFilterSummary();
    }

    function applyFilterProjection() {
      rebuildRetainedOwners();
      if (hasFullLayout) updateVisibilityFast();
      else applyFilterPresentation();
    }

    const filterSection = document.createElement("section");
    filterSection.className = "sidebar-section filter-panel";
    filterSection.dataset.sectionId = "filters";
    filterSection.innerHTML = `
      <div class="drag-handle" draggable="true" title="Drag to reorder">⋮⋮</div>
      <div class="filter-search-row">
        <input id="graph-filter-search" class="filter-search" type="search" aria-label="Search nodes and relations"
          placeholder="Find nodes or relations" />
      </div>
      <div class="filter-search-row detail-level-row" style="margin-top:6px">
        <label for="graph-detail-level">Visible detail</label>
        <select id="graph-detail-level" class="filter-mode" aria-label="Visible graph detail level"></select>
        <button id="filter-clear" class="filter-action" type="button" style="margin-left:auto">Clear</button>
      </div>
      <div id="filter-legend-slot"></div>
      <div id="filter-chips" class="filter-chips"></div>
      <div id="filter-summary" class="filter-summary" role="status" aria-live="polite"></div>`;
    const legendSection = panelContent.querySelector('[data-section-id="legend"]');
    panelContent.insertBefore(filterSection, legendSection || panelContent.firstChild);
    const filterLegendSlot = filterSection.querySelector("#filter-legend-slot");
    filterLegendSlot.appendChild(legend);
    if (legendSection) legendSection.remove();

    const filterSearchInput = filterSection.querySelector("#graph-filter-search");
    const detailLevelSelect = filterSection.querySelector("#graph-detail-level");
    const filterClearButton = filterSection.querySelector("#filter-clear");
    const filterChipsEl = filterSection.querySelector("#filter-chips");
    const filterSummaryEl = filterSection.querySelector("#filter-summary");
    filterSearchInput.placeholder = docData.ui?.filtering?.search_placeholder || "Find nodes or relations";
    detailLevels.forEach(level => {
      const option = document.createElement("option");
      option.value = String(level.id);
      option.textContent = String(level.label || level.id);
      option.title = String(level.description || "");
      detailLevelSelect.appendChild(option);
    });
    if (!detailLevels.length) detailLevelSelect.closest(".detail-level-row").hidden = true;

    function filterLabel(value) {
      return String(value).replace(/[-_]+/g, " ").replace(/\b\w/g, letter => letter.toUpperCase());
    }

    function descendantsOf(categoryId, children = categoryChildren) {
      const result = [];
      const stack = [...(children.get(categoryId) || [])];
      while (stack.length) {
        const child = stack.pop();
        result.push(child);
        stack.push(...(children.get(child) || []));
      }
      return result;
    }

    function hasExcludedCategoryAncestor(categoryId) {
      let current = String(categoryCatalog.get(categoryId)?.parent || "");
      const seen = new Set();
      while (current && !seen.has(current)) {
        if (filterState.excludedCategories.has(current)) return true;
        seen.add(current);
        current = String(categoryCatalog.get(current)?.parent || "");
      }
      return false;
    }

    function appendFacet(title, values, stateKey, options = {}) {
      if (!values.length) return;
      const facet = document.createElement("div");
      facet.className = "filter-facet";
      facet.setAttribute("role", "group");
      facet.setAttribute("aria-label", title);
      facet.innerHTML = `<div class="filter-facet-title" role="heading" aria-level="3">${escapeHtml(title)}</div>`;
      values.forEach(value => {
        const row = document.createElement("label");
        row.className = "filter-option";
        row.dataset.depth = String(options.depth?.get(value) || 0);
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        const excludedByAncestor = options.hasExcludedAncestor
          ? options.hasExcludedAncestor(value)
          : stateKey === "excludedCategories" && hasExcludedCategoryAncestor(value);
        checkbox.checked = !filterState[stateKey].has(value) && !excludedByAncestor;
        checkbox.disabled = excludedByAncestor;
        checkbox.dataset.filterKey = stateKey;
        checkbox.dataset.filterValue = value;
        const descendants = options.hierarchical
          ? descendantsOf(value, options.children || categoryChildren)
          : [];
        if (descendants.length) {
          const selectedChildren = descendants.filter(child => !filterState[stateKey].has(child)).length;
          checkbox.indeterminate = selectedChildren > 0 && selectedChildren < descendants.length;
        }
        checkbox.addEventListener("change", () => mutateFilter(() => {
          const affected = [value, ...(options.hierarchical ? descendants : [])];
          if (checkbox.checked) {
            affected.forEach(item => filterState[stateKey].delete(item));
          } else {
            filterState[stateKey].add(value);
            descendants.forEach(item => filterState[stateKey].delete(item));
          }
        }));
        const label = options.labels?.get(value) || filterLabel(value);
        row.append(checkbox, document.createTextNode(label));
        facet.appendChild(row);
      });
      filterFacetsEl.appendChild(facet);
    }

    function categoryTreeOrder() {
      const order = [];
      const depth = new Map();
      const visit = (id, level) => {
        order.push(id);
        depth.set(id, Math.min(level, 2));
        (categoryChildren.get(id) || []).sort().forEach(child => visit(child, level + 1));
      };
      Array.from(categoryCatalog.keys())
        .filter(id => !categoryCatalog.get(id)?.parent)
        .sort()
        .forEach(id => visit(id, 0));
      return {order, depth};
    }

    function edgeCategoryTreeOrder() {
      const relevant = relevantEdgeCategoryIds();
      const order = [];
      const depth = new Map();
      const visit = (id, level) => {
        if (!relevant.has(id)) return;
        order.push(id);
        depth.set(id, Math.min(level, 2));
        (edgeCategoryChildren.get(id) || []).sort().forEach(child => visit(child, level + 1));
      };
      Array.from(relevant)
        .filter(id => !edgeCategoryParent.has(id))
        .sort()
        .forEach(id => visit(id, 0));
      return {order, depth};
    }

    function addFilterChip(label, remove) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "filter-chip";
      chip.textContent = `${label} ×`;
      chip.setAttribute("aria-label", `Remove filter: ${label}`);
      chip.addEventListener("click", () => mutateFilter(remove));
      filterChipsEl.appendChild(chip);
    }

    function refreshFilterControls() {
      filterSearchInput.value = filterState.query;
      detailLevelSelect.value = filterState.detailLevel || "";
      filterChipsEl.innerHTML = "";
      [
        ["excludedCategories", "Category"], ["excludedEdgeTypes", "Relation"],
      ].forEach(([key, label]) => {
        Array.from(filterState[key]).sort().forEach(value => {
          addFilterChip(`${label}: ${filterLabel(value)}`, () => { filterState[key].delete(value); });
        });
      });
      updateFilterSummary();
      if (typeof syncNodeLegendRows === "function") syncNodeLegendRows();
      if (typeof syncEdgeLegendRows === "function") syncEdgeLegendRows();
    }

    function updateFilterSummary() {
      if (!filterSummaryEl) return;
      const renderedNodes = docData.entities.map(entity => nodeElement(entity.id)).filter(Boolean);
      const visibleNodeIds = new Set(renderedNodes
        .filter(node => node.style.display !== "none")
        .map(node => node.dataset.nodeId));
      const visibleNodeCount = renderedNodes.length
        ? visibleNodeIds.size
        : docData.entities.filter(entity => !isHiddenNode(entity.id)).length;
      const visibleEdgeCount = edgeData.filter(edge =>
        !isHiddenEdgeType(edge) && !isHiddenNode(edge.source) && !isHiddenNode(edge.target)
      ).length;
      const derivedEdgeCount = lastRenderedEdges.filter(edge =>
        edge.derived && !isHiddenEdgeType(edge) && !isHiddenNode(edge.source) && !isHiddenNode(edge.target)
      ).length;
      const matchedNodes = normalizedFilterText(filterState.query)
        ? docData.entities.filter(nodeMatchesSearch).length
        : 0;
      const matchedEdges = normalizedFilterText(filterState.query)
        ? edgeData.filter(edgeMatchesSearch).length
        : 0;
      const retained = docData.entities.filter(entity => retainedOwnerIds.has(entity.id) && visibleNodeIds.has(entity.id) && nodeFailsFilter(entity)).length;
      const queryActive = Boolean(normalizedFilterText(filterState.query));
      filterSummaryEl.textContent = `Showing ${visibleNodeCount} of ${docData.entities.length} nodes and ${visibleEdgeCount} of ${edgeData.length} relations` +
        (derivedEdgeCount ? `; ${derivedEdgeCount} derived paths` : "") +
        (queryActive ? `; ${matchedNodes} node and ${matchedEdges} relation matches` : "") +
        (retained ? `; ${retained} ownership containers retained` : "") + ".";
      filterSummaryEl.title = "Eligible items match every active facet. Retained ownership containers remain visible only to preserve structural context.";
    }

    filterSearchInput.addEventListener("focus", () => {
      searchEditStartSnapshot = filterSnapshot();
    });
    filterSearchInput.addEventListener("input", () => {
      if (searchEditStartSnapshot === null) searchEditStartSnapshot = filterSnapshot();
      filterState.query = filterSearchInput.value;
      syncSearchSelection({persist: false});
      rebuildRetainedOwners();
      applyFilterPresentation();
      saveViewerState();
    });
    filterSearchInput.addEventListener("change", () => {
      commitSearchEdit();
      refreshFilterControls();
      saveViewerState();
    });
    detailLevelSelect.addEventListener("change", () => mutateFilter(() => {
      filterState.detailLevel = detailLevelRank.has(detailLevelSelect.value)
        ? detailLevelSelect.value
        : defaultDetailLevel;
    }));
    filterClearButton.addEventListener("click", () => mutateFilter(() => {
      filterState.query = "";
      filterState.excludedTypes.clear();
      filterState.excludedKinds.clear();
      filterState.excludedCategories.clear();
      filterState.excludedEdgeTypes.clear();
      replaceNodeSelectionState([], null, "explicit");
    }));
    function resetFilteringState() {
      filterState.query = "";
      filterState.detailLevel = detailLevelRank.has(String(initialVisibility.detail_level))
        ? String(initialVisibility.detail_level)
        : defaultDetailLevel;
      filterState.excludedTypes.clear();
      filterState.excludedKinds.clear();
      filterState.excludedCategories.clear();
      filterState.excludedEdgeTypes.clear();
      filterUndoStack.length = 0;
      filterRedoStack.length = 0;
      searchEditStartSnapshot = null;
      rebuildRetainedOwners();
      refreshFilterControls();
    }
    document.addEventListener("keydown", event => {
      if (quickGuideOwnsFocus()) return;
      const typing = ["input", "textarea", "select"].includes(document.activeElement?.tagName?.toLowerCase());
      if ((event.key === "/" || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k")) && !typing) {
        event.preventDefault();
        filterSearchInput.focus();
      }
    });

    rebuildRetainedOwners();
    refreshFilterControls();
