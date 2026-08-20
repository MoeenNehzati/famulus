    // ── Legend ───────────────────────────────────────────────────────────────

    function createLegendIcon(type, style) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 24 20");
      svg.setAttribute("class", "legend-icon");
      const shape = style.shape || "rect";
      const stroke = "#1f2933";
      if (style.form === "container") {
        const frame = createSvgElement("rect");
        frame.setAttribute("x", "2"); frame.setAttribute("y", "3");
        frame.setAttribute("width", "20"); frame.setAttribute("height", "15");
        frame.setAttribute("rx", "2");
        frame.setAttribute("fill", style.color);
        frame.setAttribute("fill-opacity", style.tone === "strong" ? "0.2" : "0.07");
        frame.setAttribute("stroke", style.color);
        frame.setAttribute("stroke-width", style.tone === "strong" ? "1.8" : "2.4");
        const header = createSvgElement("path");
        header.setAttribute("d", "M 2 7 L 22 7");
        header.setAttribute("stroke", style.color);
        header.setAttribute("stroke-width", style.tone === "strong" ? "2.4" : "1.5");
        svg.appendChild(frame);
        svg.appendChild(header);
        return svg;
      }
      function add(el) {
        el.setAttribute("fill", style.color);
        el.setAttribute("stroke", stroke);
        el.setAttribute("stroke-width", "1.6");
        svg.appendChild(el);
      }
      if (shape === "ellipse") {
        const el = createSvgElement("ellipse");
        el.setAttribute("cx", "12"); el.setAttribute("cy", "10");
        el.setAttribute("rx", "10"); el.setAttribute("ry", "7");
        add(el); return svg;
      }
      if (shape === "circle") {
        const el = createSvgElement("circle");
        el.setAttribute("cx", "12"); el.setAttribute("cy", "10"); el.setAttribute("r", "7");
        add(el); return svg;
      }
      if (shape === "diamond") {
        const el = createSvgElement("polygon");
        el.setAttribute("points", "12,2 22,10 12,18 2,10");
        add(el); return svg;
      }
      if (shape === "hexagon") {
        const el = createSvgElement("polygon");
        el.setAttribute("points", "6,2 18,2 22,10 18,18 6,18 2,10");
        add(el); return svg;
      }
      if (shape === "parallelogram") {
        const el = createSvgElement("polygon");
        el.setAttribute("points", "6,2 22,2 18,18 2,18");
        add(el); return svg;
      }
      if (shape === "roundrect") {
        const el = createSvgElement("rect");
        el.setAttribute("x", "2"); el.setAttribute("y", "2");
        el.setAttribute("width", "20"); el.setAttribute("height", "16");
        el.setAttribute("rx", "5"); el.setAttribute("ry", "5");
        add(el); return svg;
      }
      if (shape === "double-rect") {
        const outer = createSvgElement("rect");
        outer.setAttribute("x", "2"); outer.setAttribute("y", "2");
        outer.setAttribute("width", "20"); outer.setAttribute("height", "16");
        add(outer);
        const inner = createSvgElement("rect");
        inner.setAttribute("x", "4.5"); inner.setAttribute("y", "4.5");
        inner.setAttribute("width", "15"); inner.setAttribute("height", "11");
        inner.setAttribute("fill", "none"); inner.setAttribute("stroke", stroke);
        inner.setAttribute("stroke-width", "1.4");
        svg.appendChild(inner); return svg;
      }
      const el = createSvgElement("rect");
      el.setAttribute("x", "2"); el.setAttribute("y", "2");
      el.setAttribute("width", "20"); el.setAttribute("height", "16");
      add(el); return svg;
    }

    function createLegendGroupTitle(text) {
      const title = document.createElement("div");
      title.className = "legend-group-title";
      title.textContent = text;
      return title;
    }

    function createLegendDisclosure(section, title) {
      const disclosure = document.createElement("details");
      disclosure.className = "legend-disclosure";
      disclosure.dataset.legendSection = section;
      disclosure.open = true;
      const summary = document.createElement("summary");
      summary.className = "legend-group-title";
      summary.textContent = title;
      disclosure.appendChild(summary);
      return disclosure;
    }

    function createEdgeLegendIcon(edgeType, style) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 32 20");
      svg.setAttribute("class", "legend-icon");
      const line = createSvgElement("path");
      line.setAttribute("d", "M 3 10 L 29 10");
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", (style && (style.stroke || style.color)) || "#0f172a");
      line.setAttribute("stroke-width", "2.4");
      if (style && style.dash) line.setAttribute("stroke-dasharray", style.dash);
      svg.appendChild(line);
      const arrow = createSvgElement("polygon");
      arrow.setAttribute("points", "29,10 23,6 23,14");
      arrow.setAttribute("fill", (style && (style.stroke || style.color)) || "#0f172a");
      svg.appendChild(arrow);
      return svg;
    }

    function bindLegendTooltip(row, label, description) {
      if (!description) return;
      row.addEventListener("mouseenter", event => {
        tooltip.innerHTML = `<strong>${escapeHtml(label)}</strong><br>${escapeHtml(description)}`;
        positionTooltip(event);
        tooltip.style.display = "block";
      });
      row.addEventListener("mousemove", event => {
        positionTooltip(event);
      });
      row.addEventListener("mouseleave", () => {
        tooltip.style.display = "none";
      });
    }

    // Only build legend rows for categories and edge types actually present in the document.
    const presentCategories = new Set(presentCategoryIds);
    const nodeLegendColumn = document.createElement("div");
    nodeLegendColumn.className = "legend-column";
    function visibleNodesInCategory(categoryId) {
      const includedCategories = new Set([categoryId, ...descendantsOf(categoryId)]);
      return docData.entities
        .filter(entity => includedCategories.has(String(entity.category || entity.type || "unknown")))
        .map(entity => entity.id)
        .filter(nodeId => !isHiddenNode(nodeId));
    }

    function visibleNodesWithKind(kind) {
      return docData.entities
        .filter(entity => kindComponents(entity.kind || entity.category || entity.type || "unknown").includes(kind))
        .map(entity => entity.id)
        .filter(nodeId => !isHiddenNode(nodeId));
    }

    function syncNodeLegendRows() {
      legend.querySelectorAll(".legend-row[data-legend-kind='node']").forEach(row => {
        const value = row.dataset.type;
        const nodeIds = row.dataset.legendFacet === "kind"
          ? visibleNodesWithKind(value)
          : visibleNodesInCategory(value);
        const selected = nodeIds.length > 0 && nodeIds.every(nodeId => selectedNodeIds.has(nodeId));
        row.classList.toggle("legend-selected", selected);
        row.setAttribute("aria-pressed", selected ? "true" : "false");
        row.setAttribute("aria-disabled", nodeIds.length ? "false" : "true");
        row.tabIndex = nodeIds.length ? 0 : -1;
      });
    }

    function selectNodeLegendCategory(categoryId, additive) {
      const categoryNodeIds = visibleNodesInCategory(categoryId);
      if (!categoryNodeIds.length) return;
      const categoryNodeSet = new Set(categoryNodeIds);
      const categorySelected = categoryNodeIds.every(nodeId => selectedNodeIds.has(nodeId));
      if (!additive) {
        if (categorySelected) {
          const remaining = Array.from(selectedNodeIds).filter(nodeId => !categoryNodeSet.has(nodeId));
          setNodeSelection(remaining, remaining.at(-1) || null, "explicit");
          return;
        }
        setNodeSelection(categoryNodeIds, categoryNodeIds.at(-1), "explicit");
        return;
      }
      const next = new Set(selectedNodeIds);
      categoryNodeIds.forEach(nodeId => {
        if (categorySelected) next.delete(nodeId);
        else next.add(nodeId);
      });
      setNodeSelection(next, next.has(selectedNodeId) ? selectedNodeId : Array.from(next).at(-1), "explicit");
    }

    function selectNodeLegendKind(kind, additive) {
      const nodeIds = visibleNodesWithKind(kind);
      if (!nodeIds.length) return;
      const nodeSet = new Set(nodeIds);
      const allSelected = nodeIds.every(nodeId => selectedNodeIds.has(nodeId));
      if (!additive) {
        if (allSelected) {
          const remaining = Array.from(selectedNodeIds).filter(nodeId => !nodeSet.has(nodeId));
          setNodeSelection(remaining, remaining.at(-1) || null, "explicit");
        } else {
          setNodeSelection(nodeIds, nodeIds.at(-1), "explicit");
        }
        return;
      }
      const next = new Set(selectedNodeIds);
      nodeIds.forEach(nodeId => allSelected ? next.delete(nodeId) : next.add(nodeId));
      setNodeSelection(next, next.has(selectedNodeId) ? selectedNodeId : Array.from(next).at(-1), "explicit");
    }

    let activeNodeParent = null;
    Object.entries(typeStyles)
      .filter(([type]) => presentCategories.has(type))
      .sort(([left], [right]) => {
        const leftParent = String(categoryCatalog.get(left)?.parent || "");
        const rightParent = String(categoryCatalog.get(right)?.parent || "");
        return leftParent.localeCompare(rightParent) || left.localeCompare(right);
      })
      .forEach(([type, style]) => {
      if (!presentCategories.has(type)) return;
      const parentId = String(categoryCatalog.get(type)?.parent || "");
      if (parentId && parentId !== activeNodeParent) {
        const parent = categoryCatalog.get(parentId) || {};
        const heading = document.createElement("div");
        heading.className = "legend-row legend-subgroup-title legend-parent-row";
        heading.textContent = `${parent.label || parentId}:`;
        heading.tabIndex = 0;
        heading.setAttribute("role", "button");
        heading.setAttribute("aria-pressed", "false");
        heading.dataset.legendKind = "node";
        heading.dataset.type = parentId;
        heading.addEventListener("click", event => {
          selectNodeLegendCategory(parentId, event.ctrlKey || event.metaKey);
        });
        heading.addEventListener("keydown", event => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          selectNodeLegendCategory(parentId, event.ctrlKey || event.metaKey);
        });
        bindLegendTooltip(heading, parent.label || parentId, parent.description || "");
        nodeLegendColumn.appendChild(heading);
        activeNodeParent = parentId;
      }
      const row = document.createElement("div");
      row.className = "legend-row";
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.setAttribute("aria-pressed", "false");
      row.addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        selectNodeLegendCategory(type, event.ctrlKey || event.metaKey);
      });
      row.dataset.legendKind = "node";
      row.dataset.type = type;
      row.appendChild(createLegendIcon(type, style));
      const label = document.createElement("div");
      const labelText = categoryLabels.get(type) || type;
      label.textContent = labelText;
      row.appendChild(label);
      bindLegendTooltip(row, labelText, categoryDescriptions.get(type) || "");
      row.addEventListener("click", event => {
        selectNodeLegendCategory(type, event.ctrlKey || event.metaKey);
      });
      if (parentId) row.classList.add("legend-child-row");
      nodeLegendColumn.appendChild(row);
    });
    if (presentCategories.size > 0) {
      const nodeLegendDisclosure = createLegendDisclosure("nodes", "Nodes");
      nodeLegendDisclosure.appendChild(nodeLegendColumn);
      legend.appendChild(nodeLegendDisclosure);
    }

    const presentKinds = Array.from(new Set(docData.entities.flatMap(entity =>
      kindComponents(entity.kind || entity.category || entity.type || "unknown")
    ))).sort();
    if (presentKinds.length) {
      const colorLegendColumn = document.createElement("div");
      colorLegendColumn.className = "legend-column legend-color-column";
      presentKinds.forEach(kind => {
        const style = {color: colorByKind.get(kind) || "#566573", shape: "rect"};
        const row = document.createElement("div");
        row.className = "legend-row";
        row.tabIndex = 0;
        row.setAttribute("role", "button");
        row.setAttribute("aria-pressed", "false");
        row.dataset.legendKind = "node";
        row.dataset.legendFacet = "kind";
        row.dataset.type = kind;
        row.appendChild(createLegendIcon(kind, {...style, shape: "rect", form: "node"}));
        const label = document.createElement("div");
        label.textContent = kind.replace(/[-_]+/g, " ").replace(/\b\w/g, letter => letter.toUpperCase());
        row.appendChild(label);
        row.addEventListener("click", event => selectNodeLegendKind(kind, event.ctrlKey || event.metaKey));
        row.addEventListener("keydown", event => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          selectNodeLegendKind(kind, event.ctrlKey || event.metaKey);
        });
        colorLegendColumn.appendChild(row);
      });
      const colorLegendDisclosure = createLegendDisclosure("colors", "Colors");
      colorLegendDisclosure.appendChild(colorLegendColumn);
      legend.appendChild(colorLegendDisclosure);
    }
    syncNodeLegendRows();

    const relevantLegendEdgeTypes = relevantEdgeCategoryIds();
    const legendEdgeTypes = [];
    const visitEdgeCategory = (edgeType, depth) => {
      if (!relevantLegendEdgeTypes.has(edgeType)) return;
      legendEdgeTypes.push({edgeType, depth: Math.min(depth, 2)});
      (edgeCategoryChildren.get(edgeType) || []).sort().forEach(child => visitEdgeCategory(child, depth + 1));
    };
    Array.from(relevantLegendEdgeTypes)
      .filter(edgeType => !edgeCategoryParent.has(edgeType))
      .sort()
      .forEach(edgeType => visitEdgeCategory(edgeType, 0));

    function syncEdgeLegendRows() {
      legend.querySelectorAll(".legend-row[data-legend-kind='edge']").forEach(row => {
        const edgeType = row.dataset.type;
        const inactive = edgeCategorySetContains(edgeType, filterState.excludedEdgeTypes);
        const blockedByParent = edgeCategorySetContains(
          edgeCategoryParent.get(edgeType), filterState.excludedEdgeTypes
        );
        row.classList.toggle("inactive", inactive);
        row.setAttribute("aria-pressed", inactive ? "false" : "true");
        row.setAttribute("aria-disabled", blockedByParent ? "true" : "false");
        row.tabIndex = blockedByParent ? -1 : 0;
        row.querySelectorAll(".relation-traverse-button").forEach(button => {
          button.disabled = blockedByParent || inactive || selectedNodeIds.size === 0;
        });
      });
    }

    function relationConstituentsForCategory(edge, edgeType) {
      return edgeConstituents(edge).filter(constituent =>
        edgeCategorySetContains(String(constituent.type || edge.type || "unknown"), new Set([edgeType]))
      );
    }

    function selectRelationTraversal(edgeType, direction) {
      const seeds = Array.from(selectedNodeIds).filter(nodeId => !isHiddenNode(nodeId));
      if (!seeds.length) return;
      const adjacency = new Map();
      edgeLayer.querySelectorAll(".edge-path").forEach(path => {
        if (path.style.display === "none") return;
        const edge = path.__edgeMeta;
        if (!edge) return;
        relationConstituentsForCategory(edge, edgeType).forEach(constituent => {
          const source = String(constituent.source || edge.source);
          const target = String(constituent.target || edge.target);
          const from = direction === "ancestors" ? target : source;
          const to = direction === "ancestors" ? source : target;
          if (isHiddenNode(from) || isHiddenNode(to)) return;
          if (!adjacency.has(from)) adjacency.set(from, new Set());
          adjacency.get(from).add(to);
        });
      });
      const reached = new Set(seeds);
      const queue = [...seeds];
      while (queue.length) {
        const current = queue.shift();
        for (const next of adjacency.get(current) || []) {
          if (reached.has(next)) continue;
          reached.add(next);
          queue.push(next);
        }
      }
      setNodeSelection(reached, selectedNodeId, "explicit");
    }

    const edgeLegendColumn = document.createElement("div");
    edgeLegendColumn.className = "legend-column";
    if (legendEdgeTypes.length > 0) edgeLegendColumn.appendChild(createLegendGroupTitle("Relations"));
    legendEdgeTypes.forEach(({edgeType, depth}) => {
      const style = edgeStyleForType(edgeType);
      const row = document.createElement("div");
      row.className = "legend-row";
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.setAttribute("aria-pressed", edgeCategorySetContains(edgeType, filterState.excludedEdgeTypes) ? "false" : "true");
      row.addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        row.click();
      });
      row.dataset.legendKind = "edge";
      row.dataset.type = edgeType;
      row.dataset.depth = String(depth);
      row.style.paddingLeft = `${8 + depth * 16}px`;
      row.appendChild(createEdgeLegendIcon(edgeType, style));
      const label = document.createElement("div");
      const category = edgeCategoryCatalog.get(edgeType) || {};
      const labelText = category.label || edgeType;
      label.textContent = labelText;
      row.appendChild(label);
      const actions = document.createElement("div");
      actions.className = "legend-relation-actions";
      [["ancestors", "←", "Select ancestors"], ["successors", "→", "Select successors"]]
        .forEach(([direction, symbol, actionLabel]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "relation-traverse-button";
          button.dataset.direction = direction;
          button.textContent = symbol;
          button.title = `${actionLabel} through ${labelText}`;
          button.setAttribute("aria-label", `${actionLabel} through ${labelText}`);
          button.addEventListener("click", event => {
            event.stopPropagation();
            selectRelationTraversal(edgeType, direction);
          });
          actions.appendChild(button);
        });
      row.appendChild(actions);
      bindLegendTooltip(row, labelText, category.description || "");
      row.addEventListener("click", () => {
        if (edgeCategorySetContains(
          edgeCategoryParent.get(edgeType), filterState.excludedEdgeTypes
        )) return;
        mutateFilter(() => {
          if (filterState.excludedEdgeTypes.has(edgeType)) {
            filterState.excludedEdgeTypes.delete(edgeType);
          } else {
            filterState.excludedEdgeTypes.add(edgeType);
            descendantsOf(edgeType, edgeCategoryChildren).forEach(
              child => filterState.excludedEdgeTypes.delete(child)
            );
          }
        });
      });
      edgeLegendColumn.appendChild(row);
    });
    if (legendEdgeTypes.length > 0) legend.appendChild(edgeLegendColumn);
    syncEdgeLegendRows();

    const edgePresentationLegendColumn = document.createElement("div");
    edgePresentationLegendColumn.className = "legend-column legend-edge-presentation-column";
    legend.appendChild(edgePresentationLegendColumn);

    function syncEdgePresentationLegend() {
      const presentStateIds = new Set();
      const representativeEdges = new Map();
      edgeLayer.querySelectorAll(".edge-path").forEach(path => {
        if (path.style.display === "none" || !path.__edgeMeta) return;
        edgeMetadataPresentationIds(path.__edgeMeta).forEach(stateId => {
          presentStateIds.add(stateId);
          if (!representativeEdges.has(stateId)) representativeEdges.set(stateId, path.__edgeMeta);
        });
      });
      edgePresentationLegendColumn.replaceChildren();
      edgePresentationLegendColumn.hidden = false;
      edgePresentationLegendColumn.appendChild(createLegendGroupTitle("Edge presentation"));
      edgePresentationFacets.forEach(facet => {
        const presentVariants = new Map();
        edgeLayer.querySelectorAll(".edge-path").forEach(path => {
          if (path.style.display === "none" || !path.__edgeMeta) return;
          matchedEdgePresentationVariants(path.__edgeMeta)
            .filter(match => match.facet.id === facet.id)
            .forEach(match => presentVariants.set(match.variant.id, match.variant));
        });
        if (!presentVariants.size) return;
        edgePresentationLegendColumn.appendChild(createLegendGroupTitle(facet.label));
        facet.variants.forEach(variant => {
          if (!presentVariants.has(variant.id)) return;
          const row = document.createElement("div");
          row.className = "legend-row legend-explanation-row";
          row.tabIndex = 0;
          row.setAttribute("role", "note");
          row.setAttribute("aria-label", `${variant.label}: ${variant.description}`);
          row.dataset.legendKind = "edge-presentation-variant";
          row.dataset.facet = facet.id;
          row.dataset.type = variant.id;
          row.appendChild(createDeclaredEdgePresentationLegendIcon(variant));
          const label = document.createElement("div");
          label.textContent = variant.label;
          row.appendChild(label);
          bindLegendTooltip(row, variant.label, variant.description);
          edgePresentationLegendColumn.appendChild(row);
        });
      });
      Array.from(edgeMetadataStyleCatalog)
        .forEach(([stateId, rule]) => {
          const present = presentStateIds.has(stateId);
          const row = document.createElement("div");
          row.className = `legend-row legend-explanation-row${present ? "" : " unavailable"}`;
          row.tabIndex = 0;
          row.setAttribute("role", "note");
          row.setAttribute("aria-label", `${rule.label}: ${rule.description}${present ? "" : " Not present in the current graph."}`);
          row.dataset.legendKind = "edge-presentation";
          row.dataset.type = stateId;
          row.dataset.present = present ? "true" : "false";
          row.appendChild(createEdgePresentationLegendIcon(stateId, rule, representativeEdges.get(stateId)));
          const label = document.createElement("div");
          label.textContent = rule.label;
          row.appendChild(label);
          bindLegendTooltip(row, rule.label, rule.description);
          edgePresentationLegendColumn.appendChild(row);
        });
    }
