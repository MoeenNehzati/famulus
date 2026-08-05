    // ── Node rendering ───────────────────────────────────────────────────────

    const nodeGradientIds = new Map();

    function nodeFill(style) {
      const colors = Array.isArray(style.colors) ? style.colors.filter(Boolean) : [];
      if (colors.length < 2) return style.color;
      const key = colors.join("|");
      if (!nodeGradientIds.has(key)) {
        const id = `node-kind-gradient-${nodeGradientIds.size}`;
        const gradient = createSvgElement("linearGradient");
        gradient.setAttribute("id", id);
        gradient.setAttribute("x1", "0%");
        gradient.setAttribute("y1", "0%");
        gradient.setAttribute("x2", "100%");
        gradient.setAttribute("y2", "100%");
        colors.forEach((color, index) => {
          const stop = createSvgElement("stop");
          stop.setAttribute("offset", `${colors.length === 1 ? 0 : index * 100 / (colors.length - 1)}%`);
          stop.setAttribute("stop-color", color);
          gradient.appendChild(stop);
        });
        svgEl.querySelector("defs").appendChild(gradient);
        nodeGradientIds.set(key, id);
      }
      return `url(#${nodeGradientIds.get(key)})`;
    }

    function applyContainerShellStyle(shapeEl, style, tone = "subtle") {
      shapeEl.setAttribute("class", "node-shape");
      shapeEl.setAttribute("fill", nodeFill(style));
      shapeEl.setAttribute("fill-opacity", tone === "strong" ? "0.16" : "0.055");
      shapeEl.setAttribute("stroke", style.color);
      shapeEl.setAttribute("stroke-width", tone === "strong" ? "2.25" : "3");
    }

    function renderContainerShell({layer, id, label, subtitle, position, style, tone = "subtle", className = ""}) {
      const group = createSvgElement("g");
      group.setAttribute("class", className);
      group.dataset.shellId = String(id);
      group.setAttribute("aria-hidden", "true");
      group.setAttribute("pointer-events", "none");
      const shape = createSvgElement("rect");
      shape.setAttribute("x", position.x);
      shape.setAttribute("y", position.y);
      shape.setAttribute("width", position.width);
      shape.setAttribute("height", position.height);
      applyContainerShellStyle(shape, style, tone);
      group.appendChild(shape);
      const foreignObject = createSvgElement("foreignObject");
      foreignObject.setAttribute("x", position.x);
      foreignObject.setAttribute("y", position.y);
      foreignObject.setAttribute("width", position.width);
      foreignObject.setAttribute("height", Math.min(position.height, 58));
      const body = document.createElementNS("http://www.w3.org/1999/xhtml", "div");
      body.setAttribute("class", "node-fo-body container-node");
      body.innerHTML = `<div class="node-label">${escapeHtml(label)}</div><div class="node-subtitle">${escapeHtml(subtitle || "")}</div>`;
      foreignObject.appendChild(body);
      group.appendChild(foreignObject);
      layer.appendChild(group);
      return group;
    }

    function expandSelectionRing(ring, x, y, w, h, shape) {
      const gap = SELECTION_RING_GAP;
      const tag = ring.tagName.toLowerCase();
      if (tag === "rect") {
        ring.setAttribute("x", x - gap);
        ring.setAttribute("y", y - gap);
        ring.setAttribute("width", w + 2 * gap);
        ring.setAttribute("height", h + 2 * gap);
        if (shape === "roundrect") {
          ring.setAttribute("rx", 18 + gap);
          ring.setAttribute("ry", 18 + gap);
        }
        return;
      }
      if (tag === "circle") {
        const r = Number(ring.getAttribute("r") || 0);
        ring.setAttribute("r", r + gap);
        return;
      }
      if (tag === "ellipse") {
        const rx = Number(ring.getAttribute("rx") || 0);
        const ry = Number(ring.getAttribute("ry") || 0);
        ring.setAttribute("rx", rx + gap);
        ring.setAttribute("ry", ry + gap);
        return;
      }
      if (tag === "polygon") {
        const rawPoints = (ring.getAttribute("points") || "")
          .trim()
          .split(/\s+/)
          .map(pair => pair.split(",").map(Number))
          .filter(pair => pair.length === 2 && Number.isFinite(pair[0]) && Number.isFinite(pair[1]));
        if (rawPoints.length < 3) return;

        const signedArea = rawPoints.reduce((sum, [x1, y1], idx) => {
          const [x2, y2] = rawPoints[(idx + 1) % rawPoints.length];
          return sum + x1 * y2 - x2 * y1;
        }, 0);
        const outwardSign = signedArea >= 0 ? 1 : -1;

        const offsetLines = rawPoints.map(([x1, y1], idx) => {
          const [x2, y2] = rawPoints[(idx + 1) % rawPoints.length];
          const dx = x2 - x1;
          const dy = y2 - y1;
          const length = Math.hypot(dx, dy) || 1;
          const nx = outwardSign * dy / length;
          const ny = -outwardSign * dx / length;
          return {
            p: { x: x1 + gap * nx, y: y1 + gap * ny },
            d: { x: dx, y: dy }
          };
        });

        function lineIntersection(lineA, lineB, fallback) {
          const cross = lineA.d.x * lineB.d.y - lineA.d.y * lineB.d.x;
          if (Math.abs(cross) < 1e-6) return fallback;
          const px = lineB.p.x - lineA.p.x;
          const py = lineB.p.y - lineA.p.y;
          const t = (px * lineB.d.y - py * lineB.d.x) / cross;
          return { x: lineA.p.x + t * lineA.d.x, y: lineA.p.y + t * lineA.d.y };
        }

        const expanded = rawPoints.map((point, idx) => {
          const prev = offsetLines[(idx + rawPoints.length - 1) % rawPoints.length];
          const current = offsetLines[idx];
          return lineIntersection(prev, current, { x: point[0], y: point[1] });
        }).map(point => `${point.x},${point.y}`);
        if (expanded.length) ring.setAttribute("points", expanded.join(" "));
      }
    }

    function nodePresentationState(entity, {forceContainer = false} = {}) {
      const presentation = entity.presentation || {};
      const isContainer = forceContainer || presentation.form === "container" || isContainerNode(entity.id);
      const directChildren = docData.entities.filter(
        candidate => parentByNode.get(candidate.id) === entity.id
      );
      const hasVisibleChildren = directChildren.some(candidate => !isHiddenNode(candidate.id));
      const descendants = [...directChildren];
      const seenDescendants = new Set();
      let hasDetailHiddenDescendant = false;
      while (descendants.length && !hasDetailHiddenDescendant) {
        const descendant = descendants.pop();
        if (!descendant || seenDescendants.has(descendant.id)) continue;
        seenDescendants.add(descendant.id);
        if (nodeHiddenByDetailLevel(descendant.id)) {
          hasDetailHiddenDescendant = true;
          break;
        }
        docData.entities.forEach(candidate => {
          if (parentByNode.get(candidate.id) === descendant.id) descendants.push(candidate);
        });
      }
      let containmentDepth = 0;
      let ancestorId = parentByNode.get(entity.id);
      const seenAncestors = new Set();
      while (ancestorId && !seenAncestors.has(ancestorId)) {
        seenAncestors.add(ancestorId);
        containmentDepth += 1;
        ancestorId = parentByNode.get(ancestorId);
      }
      const detailPromoted = isContainer && hasDetailHiddenDescendant;
      const detailPromotionClasses = detailPromoted
        ? ` detail-promoted detail-depth-${Math.min(containmentDepth, 2)}${hasVisibleChildren ? " detail-promoted-branch" : " detail-promoted-leaf"}`
        : "";
      return {
        isContainer,
        containmentDepth,
        detailPromoted,
        hasVisibleChildren,
        className: `${isContainer ? "node-fo-body container-node" : "node-fo-body"}${detailPromotionClasses}`,
      };
    }

    function renderNode(entity, position) {
      const presentation = entity.presentation || {};
      const presentationState = nodePresentationState(entity);
      const isContainer = presentationState.isContainer;
      const containerTone = presentation.tone || "subtle";
      const baseStyle = nodeStyle(entity);
      const style = {...baseStyle, shape: isContainer ? "rect" : baseStyle.shape};
      const decorations = Array.isArray(entity.decorations) ? entity.decorations : [];
      const offsetDecoration = decorations.find(
        decoration => decoration.type === "outline" && decoration.style === "offset"
      );
      const x = position.x, y = position.y;
      const fallbackDimensions = defaultNodeDimensions(entity.id);
      const w = position.width || fallbackDimensions.width;
      const h = position.height || fallbackDimensions.height;
      const stroke = "#1f2933";
      const isInferred = entity.source === "inferred";
      const group = createSvgElement("g");
      group.setAttribute("class", isContainer ? "graph-node container-node" : "graph-node");
      group.dataset.nodeId = entity.id;
      if (isContainer) {
        group.dataset.containmentDepth = String(presentationState.containmentDepth);
      }

      let shapeEl = null;
      let selectionRing = null;
      if (style.shape === "ellipse") {
        shapeEl = createSvgElement("ellipse");
        shapeEl.setAttribute("cx", x + w / 2); shapeEl.setAttribute("cy", y + h / 2);
        shapeEl.setAttribute("rx", w / 2); shapeEl.setAttribute("ry", h / 2);
        selectionRing = shapeEl.cloneNode(false);
      } else if (style.shape === "circle") {
        shapeEl = createSvgElement("circle");
        shapeEl.setAttribute("cx", x + w / 2); shapeEl.setAttribute("cy", y + h / 2);
        shapeEl.setAttribute("r", Math.min(w, h) / 2);
        selectionRing = shapeEl.cloneNode(false);
      } else if (style.shape === "diamond") {
        shapeEl = createSvgElement("polygon");
        shapeEl.setAttribute("points", `${x + w / 2},${y} ${x + w},${y + h / 2} ${x + w / 2},${y + h} ${x},${y + h / 2}`);
        selectionRing = shapeEl.cloneNode(false);
      } else if (style.shape === "hexagon") {
        const inset = 26;
        shapeEl = createSvgElement("polygon");
        shapeEl.setAttribute("points", `${x + inset},${y} ${x + w - inset},${y} ${x + w},${y + h / 2} ${x + w - inset},${y + h} ${x + inset},${y + h} ${x},${y + h / 2}`);
        selectionRing = shapeEl.cloneNode(false);
      } else if (style.shape === "parallelogram") {
        const skew = 20;
        shapeEl = createSvgElement("polygon");
        shapeEl.setAttribute("points", `${x + skew},${y} ${x + w},${y} ${x + w - skew},${y + h} ${x},${y + h}`);
        selectionRing = shapeEl.cloneNode(false);
      } else if (style.shape === "roundrect") {
        shapeEl = createSvgElement("rect");
        shapeEl.setAttribute("x", x); shapeEl.setAttribute("y", y);
        shapeEl.setAttribute("width", w); shapeEl.setAttribute("height", h);
        shapeEl.setAttribute("rx", 18); shapeEl.setAttribute("ry", 18);
        selectionRing = shapeEl.cloneNode(false);
      } else if (style.shape === "double-rect") {
        const outer = createSvgElement("rect");
        outer.setAttribute("x", x); outer.setAttribute("y", y);
        outer.setAttribute("width", w); outer.setAttribute("height", h);
        outer.setAttribute("class", "node-shape");
        outer.setAttribute("fill", nodeFill(style)); outer.setAttribute("stroke", stroke);
        outer.setAttribute("stroke-width", "2");
        if (isInferred) outer.setAttribute("stroke-dasharray", "6 3");
        group.appendChild(outer);
        const inner = createSvgElement("rect");
        inner.setAttribute("x", x + 6); inner.setAttribute("y", y + 6);
        inner.setAttribute("width", w - 12); inner.setAttribute("height", h - 12);
        inner.setAttribute("fill", "none"); inner.setAttribute("stroke", stroke);
        inner.setAttribute("stroke-width", "2");
        group.appendChild(inner);
        selectionRing = outer.cloneNode(false);
      } else {
        shapeEl = createSvgElement("rect");
        shapeEl.setAttribute("x", x); shapeEl.setAttribute("y", y);
        shapeEl.setAttribute("width", w); shapeEl.setAttribute("height", h);
        selectionRing = shapeEl.cloneNode(false);
      }

      if (selectionRing) {
        expandSelectionRing(selectionRing, x, y, w, h, style.shape);
        selectionRing.setAttribute("class", "selection-ring");
        group.appendChild(selectionRing);
      }

      if (shapeEl) {
        if (isContainer) {
          applyContainerShellStyle(shapeEl, style, containerTone);
        } else {
          shapeEl.setAttribute("class", "node-shape");
          shapeEl.setAttribute("fill", nodeFill(style));
          shapeEl.setAttribute("stroke", stroke);
          shapeEl.setAttribute("stroke-width", "2");
        }
        if (isInferred) shapeEl.setAttribute("stroke-dasharray", "6 3");
        group.appendChild(shapeEl);
      }

      if (offsetDecoration) {
        const plate = createSvgElement("rect");
        plate.setAttribute("x", x + 5);
        plate.setAttribute("y", y - 5);
        plate.setAttribute("width", w);
        plate.setAttribute("height", h);
        plate.setAttribute("rx", "8");
        plate.setAttribute("ry", "8");
        plate.setAttribute("fill", "#f8fafc");
        plate.setAttribute("stroke", style.color || stroke);
        plate.setAttribute("stroke-width", "2.5");
        plate.setAttribute("pointer-events", "none");
        plate.setAttribute("aria-hidden", "true");
        group.insertBefore(plate, group.firstChild);
      }

      const foreignObject = createSvgElement("foreignObject");
      foreignObject.setAttribute("x", x); foreignObject.setAttribute("y", y);
      foreignObject.setAttribute("width", w); foreignObject.setAttribute("height", h);
      const body = document.createElementNS("http://www.w3.org/1999/xhtml", "div");
      body.setAttribute("class", presentationState.className);
      body.innerHTML = `<div class="node-label">${escapeHtml(entity.label || entity.short_title)}</div><div class="node-subtitle">${escapeHtml(entity.type + (entity.ref ? " " + entity.ref : ""))}</div>`;
      foreignObject.appendChild(body);
      group.appendChild(foreignObject);
      if (offsetDecoration) {
        const count = Number(offsetDecoration.count || 0);
        const badgeLabel = `${String(offsetDecoration.label || "").toUpperCase()}${count ? ` ${count}` : ""}`;
        const badgeWidth = Math.max(58, Math.min(116, 18 + badgeLabel.length * 7));
        const badge = createSvgElement("rect");
        badge.setAttribute("x", x + w - badgeWidth - 9);
        badge.setAttribute("y", y + 7);
        badge.setAttribute("width", badgeWidth);
        badge.setAttribute("height", "20");
        badge.setAttribute("rx", "10");
        badge.setAttribute("fill", style.color || stroke);
        badge.setAttribute("pointer-events", "none");
        const badgeText = createSvgElement("text");
        badgeText.setAttribute("x", x + w - badgeWidth / 2 - 9);
        badgeText.setAttribute("y", y + 21);
        badgeText.setAttribute("text-anchor", "middle");
        badgeText.setAttribute("fill", "#ffffff");
        badgeText.setAttribute("font-size", "12");
        badgeText.setAttribute("font-weight", "700");
        badgeText.setAttribute("letter-spacing", "0.45");
        badgeText.setAttribute("pointer-events", "none");
        badgeText.textContent = badgeLabel;
        group.appendChild(badge);
        group.appendChild(badgeText);
      }
      return group;
    }
