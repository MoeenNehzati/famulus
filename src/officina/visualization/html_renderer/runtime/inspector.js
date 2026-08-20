    // ── Entity/edge display ──────────────────────────────────────────────────

    function formatLegacyEntity(entity) {
      const description = escapeHtml(entity.description || "");
      const deps = (entity.connects_to || []).map(dep => {
        const other = entityMap.get(dep.to);
        const name = other ? (other.label || other.short_title) : dep.to;
        const edgeLabel = dep.label || dep.edge_label || dep.type || "";
        const confidence = dep.confidence ? ` (confidence: ${escapeHtml(dep.confidence)})` : "";
        const metadata = dep.metadata ? `<pre>${escapeHtml(JSON.stringify(dep.metadata, null, 2))}</pre>` : "";
        return `<li><strong>${escapeHtml(name)}</strong> <code>${escapeHtml(edgeLabel)}</code><br>${escapeHtml(dep.description || "")}${confidence ? `<br><span class="small">${escapeHtml(dep.evidence || "")}${confidence}</span>` : ""}${metadata}</li>`;
      }).join("");
      return `
        <div class="details-header">
          <h2>${escapeHtml(entity.short_title)}</h2>
          <button class="deselect-btn" type="button" title="Deselect (Esc)">✕</button>
        </div>
        <div><strong>Ref:</strong> ${escapeHtml(entity.ref || "—")}</div>
        <div><strong>Type:</strong> <code>${escapeHtml(entity.type)}</code></div>
        <div><strong>Title:</strong> ${escapeHtml(entity.title || "—")}</div>
        <div><strong>Active in:</strong> ${escapeHtml(entity.active_in || "—")}</div>
        <div><strong>Source:</strong> ${escapeHtml(entity.source || "—")}</div>
        <div><strong>Defined:</strong> ${escapeHtml(entity.defined || "—")}</div>
        <p>${description}</p>
        <h3>Direct dependencies</h3>
        <ul>${deps || "<li>None</li>"}</ul>
      `;
    }

    function detailValueText(value) {
      return Array.isArray(value) ? value.join("\n") : String(value ?? "");
    }

    function formatDetailReference(value, target = null) {
      const nodeId = String(target || value);
      const label = escapeHtml(String(value));
      if (!entityMap.has(nodeId)) return `<code>${label}</code>`;
      return `<button class="detail-reference" type="button" data-detail-node-id="${escapeHtml(nodeId)}">${label}</button>`;
    }

    function formatDetailField(field) {
      const value = field.value;
      let rendered;
      if (field.format === "reference") {
        rendered = formatDetailReference(value, field.target);
      } else if (field.format === "reference-list") {
        const values = Array.isArray(value) ? value : [value];
        rendered = `<div class="detail-list">${values.map(item => formatDetailReference(item)).join("") || "None"}</div>`;
      } else if (field.format === "list") {
        const values = Array.isArray(value) ? value : [value];
        rendered = `<div class="detail-list">${values.map(item => `<span>${escapeHtml(String(item))}</span>`).join("") || "None"}</div>`;
      } else if (field.format === "code" || field.format === "path") {
        rendered = `<code>${escapeHtml(detailValueText(value))}</code>`;
      } else {
        rendered = escapeHtml(detailValueText(value));
      }
      const copy = field.copyable
        ? `<button class="detail-copy" type="button" data-copy-value="${escapeHtml(detailValueText(value))}" title="Copy value">Copy</button>`
        : "";
      return `<div class="detail-field"><div class="detail-field-label">${escapeHtml(field.label)}</div><div class="detail-field-value">${rendered}${copy}</div></div>`;
    }

    function formatRelationshipSection(entity, direction) {
      const relationships = direction === "outgoing"
        ? edgeData.filter(edge => edge.source === entity.id && !edgeSuppressedByCategorySet(edge, hiddenEdgeTypes)).map(edge => ({edge, other: edge.target}))
        : edgeData.filter(edge => edge.target === entity.id && !edgeSuppressedByCategorySet(edge, hiddenEdgeTypes)).map(edge => ({edge, other: edge.source}));
      if (!relationships.length) return "";
      const rows = relationships.map(({edge, other}) => {
        const arrow = direction === "outgoing" ? "→" : "←";
        const description = edge.description ? `<div class="small">${escapeHtml(edge.description)}</div>` : "";
        return `<li><code>${escapeHtml(edge.type || "relationship")}</code> ${arrow} ${formatDetailReference(other)}${description}</li>`;
      }).join("");
      return `<section class="detail-section"><h3>${direction === "outgoing" ? "Outgoing relationships" : "Incoming relationships"}</h3><ul class="detail-relationships">${rows}</ul></section>`;
    }

    function formatStructuredEntity(entity) {
      const detailsPayload = entity.details || {};
      const summary = detailsPayload.summary || entity.description || "";
      const category = (docData.categories || []).find(item => item.id === entity.category);
      const roleLabel = category?.label || `${entity.type}${entity.kind ? `: ${entity.kind}` : ""}`;
      const sections = (detailsPayload.sections || []).map(section => `
        <section class="detail-section">
          <h3>${escapeHtml(section.title)}</h3>
          ${(section.fields || []).map(formatDetailField).join("")}
        </section>
      `).join("");
      return `
        <div class="details-header">
          <div><h2>${escapeHtml(entity.label || entity.short_title)}</h2><div class="small">${escapeHtml(roleLabel)}</div></div>
          <button class="deselect-btn" type="button" title="Deselect (Esc)">✕</button>
        </div>
        ${summary ? `<p class="detail-summary">${escapeHtml(summary)}</p>` : ""}
        ${sections}
        ${formatRelationshipSection(entity, "outgoing")}
        ${formatRelationshipSection(entity, "incoming")}
      `;
    }

    async function copyDetailValue(value) {
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(value);
          return;
        } catch (_error) {
          // Local file viewers may deny the asynchronous Clipboard API.
        }
      }
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }

    function bindDetailPanelControls() {
      details.querySelector(".deselect-btn")?.addEventListener("click", deselect);
      details.querySelectorAll(".detail-reference").forEach(button => {
        button.addEventListener("click", () => {
          const target = entityMap.get(button.dataset.detailNodeId);
          if (!target) return;
          updateVisibilityFast();
          setNodeSelection([target.id], target.id, "explicit");
        });
      });
      details.querySelectorAll(".detail-copy").forEach(button => {
        button.addEventListener("click", async () => {
          await copyDetailValue(button.dataset.copyValue || "");
          const previous = button.textContent;
          button.textContent = "Copied";
          setTimeout(() => { button.textContent = previous; }, 900);
        });
      });
    }

    function showEntityDetails(entity) {
      clearMathBeforeMutation(details);
      details.innerHTML = entity.details ? formatStructuredEntity(entity) : formatLegacyEntity(entity);
      bindDetailPanelControls();
      typesetElement(details);
      rawJsonCodeEl.textContent = JSON.stringify(entity, null, 2);
    }

    function showEdgeDetails(edge) {
      runGraphAction(() => {
        if (typeof clearPresentationNodeSelection === "function") {
          clearPresentationNodeSelection();
        }
        replaceNodeSelectionState([], null, "explicit");
      }, {renderMode: "selection"});
      clearMathBeforeMutation(details);
      details.innerHTML = edge.bundle
        ? formatBundledEdge(edge)
        : edge.details ? formatStructuredEdge(edge) : edgeTooltipText(edge);
      bindDetailPanelControls();
      typesetElement(details);
      rawJsonCodeEl.textContent = JSON.stringify(edge, null, 2);
    }

    function deselect() {
      runGraphAction(() => {
        if (typeof clearPresentationNodeSelection === "function") {
          clearPresentationNodeSelection();
        }
        replaceNodeSelectionState([], null, "explicit");
      }, {renderMode: "selection"});
      const activeElement = document.activeElement;
      if (activeElement?.matches?.(".graph-node, .edge-path, .presentation-node-component")) activeElement.blur();
    }

    function clearSelectionDetails() {
      setNodeSelection([], null, "explicit", {persist: false});
    }

    function tooltipText(entity) {
      return `
        <strong>${escapeHtml(entity.label || entity.short_title)}</strong><br>
        ${escapeHtml(entity.ref || "")}<br>
        ${escapeHtml(entity.type)}<br>
        ${escapeHtml(entity.description || "")}
      `;
    }

    function edgeTooltipText(edge) {
      const source = entityMap.get(edge.source);
      const target = entityMap.get(edge.target);
      const sourceLabel = source ? (source.label || source.short_title) : edge.source;
      const targetLabel = target ? (target.label || target.short_title) : edge.target;
      const edgeLabel = edge.label || edge.edge_label || edge.type || "";
      const summary = edge.details?.summary || edge.description || "";
      const confidence = edge.confidence ? ` (confidence: ${escapeHtml(edge.confidence)})` : "";
      const aggregateCount = edge.aggregate
        ? `<br><strong>Aggregated summary:</strong> ${Number(edge.metadata?.represented_count || 1)} underlying relationship(s)`
        : "";
      const visibleConstituents = edgeConstituents(edge).filter(constituent =>
        !edgeSuppressedByCategorySet(constituent, hiddenEdgeTypes) && !edgeFailsFilter(constituent)
      );
      const bundleSummary = edge.bundle
        ? `<br><strong>Relationships:</strong> ${visibleConstituents.map(constituent => escapeHtml(constituent.label || constituent.type || "relationship")).join(", ")}`
        : "";
      return `
        <strong>${escapeHtml(sourceLabel)} → ${escapeHtml(targetLabel)}</strong><br>
        <code>${escapeHtml(edgeLabel)}</code><br>
        ${escapeHtml(summary)}<br>
        <span class="small">${escapeHtml(edge.evidence || "")}${confidence}</span>${aggregateCount}${bundleSummary}
      `;
    }

    function formatBundledEdge(edge) {
      const source = entityMap.get(edge.source);
      const target = entityMap.get(edge.target);
      const visibleConstituents = edgeConstituents(edge).filter(constituent =>
        !edgeSuppressedByCategorySet(constituent, hiddenEdgeTypes) && !edgeFailsFilter(constituent)
      );
      const rows = visibleConstituents.map(constituent => {
        const logicalTarget = constituent.metadata?.outside_id || constituent.target;
        const summary = constituent.details?.summary || constituent.description || "";
        return `<li><code>${escapeHtml(constituent.label || constituent.type || "relationship")}</code>` +
          `${logicalTarget !== edge.target ? ` → ${escapeHtml(logicalTarget)}` : ""}` +
          `${summary ? `<div class="small">${escapeHtml(summary)}</div>` : ""}</li>`;
      }).join("");
      return `
        <div class="details-header">
          <div><h2>Bundled relationships</h2><div class="small">${visibleConstituents.length} visible relationship${visibleConstituents.length === 1 ? "" : "s"}</div></div>
          <button class="deselect-btn" type="button" title="Deselect (Esc)">✕</button>
        </div>
        <p class="detail-summary">${escapeHtml(source?.label || source?.short_title || edge.source)} → ${escapeHtml(target?.label || target?.short_title || edge.target)}</p>
        <section class="detail-section"><h3>Underlying relationships</h3><ul class="detail-relationships">${rows || "<li>None visible</li>"}</ul></section>
      `;
    }

    function formatStructuredEdge(edge) {
      const detailsPayload = edge.details || {};
      const sections = (detailsPayload.sections || []).map(section => `
        <section class="detail-section">
          <h3>${escapeHtml(section.title)}</h3>
          ${(section.fields || []).map(formatDetailField).join("")}
        </section>
      `).join("");
      return `
        <div class="details-header">
          <div><h2>${escapeHtml(edge.label || edge.type || "Relationship")}</h2><div class="small">${escapeHtml(edge.type || "relationship")}</div></div>
          <button class="deselect-btn" type="button" title="Deselect (Esc)">✕</button>
        </div>
        ${detailsPayload.summary ? `<p class="detail-summary">${escapeHtml(detailsPayload.summary)}</p>` : ""}
        ${sections}
      `;
    }
