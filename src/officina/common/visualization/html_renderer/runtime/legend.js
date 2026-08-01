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
    if (presentCategories.size > 0) legend.appendChild(createLegendGroupTitle("Nodes"));
    Object.entries(typeStyles).forEach(([type, style]) => {
      if (!presentCategories.has(type)) return;
      const row = document.createElement("div");
      row.className = "legend-row";
      row.dataset.legendKind = "node";
      row.dataset.type = type;
      row.appendChild(createLegendIcon(type, style));
      const label = document.createElement("div");
      const labelText = categoryLabels.get(type) || type;
      label.textContent = labelText;
      row.appendChild(label);
      bindLegendTooltip(row, labelText, categoryDescriptions.get(type) || "");
      row.addEventListener("click", () => {
        if (hiddenTypes.has(type)) {
          hiddenTypes.delete(type);
          row.classList.remove("inactive");
        } else {
          hiddenTypes.add(type);
          row.classList.add("inactive");
        }
        saveViewerState();
        updateVisibilityFast();
      });
      if (hiddenTypes.has(type)) row.classList.add("inactive");
      legend.appendChild(row);
    });

    if (presentEdgeTypes.length > 0) legend.appendChild(createLegendGroupTitle("Edges"));
    presentEdgeTypes.forEach(edgeType => {
      const style = edgeStyleForType(edgeType);
      const row = document.createElement("div");
      row.className = "legend-row";
      row.dataset.legendKind = "edge";
      row.dataset.type = edgeType;
      row.appendChild(createEdgeLegendIcon(edgeType, style));
      const label = document.createElement("div");
      const category = edgeCategoryCatalog.get(edgeType) || {};
      const labelText = category.label || edgeType;
      label.textContent = labelText;
      row.appendChild(label);
      bindLegendTooltip(row, labelText, category.description || "");
      row.addEventListener("click", () => {
        if (hiddenEdgeTypes.has(edgeType)) {
          hiddenEdgeTypes.delete(edgeType);
          row.classList.remove("inactive");
        } else {
          hiddenEdgeTypes.add(edgeType);
          row.classList.add("inactive");
        }
        saveViewerState();
        updateVisibilityFast();
      });
      if (hiddenEdgeTypes.has(edgeType)) row.classList.add("inactive");
      legend.appendChild(row);
    });

