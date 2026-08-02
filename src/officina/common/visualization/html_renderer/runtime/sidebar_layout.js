    // ── Generic two-sidebar layout, responsive drawers, and resizing ──────────

    const SIDEBAR_MIN_WIDTH = 220;
    const SIDEBAR_MAX_WIDTH = 520;
    const NARROW_VIEWPORT_WIDTH = 720;
    let leftPanelWidth = 280;
    let rightPanelWidth = 300;
    let leftPanelCollapsed = false;
    let rightPanelCollapsed = false;
    let leftPanelMobileOpen = false;
    let rightPanelMobileOpen = false;

    function isNarrowViewport() {
      return window.innerWidth <= NARROW_VIEWPORT_WIDTH;
    }

    function clampSidebarWidth(value) {
      const viewportMaximum = Math.max(SIDEBAR_MIN_WIDTH, Math.floor(window.innerWidth * 0.42));
      return Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, viewportMaximum, value));
    }

    function syncSidebarLayout() {
      const narrow = isNarrowViewport();
      leftPanelWidth = clampSidebarWidth(leftPanelWidth);
      rightPanelWidth = clampSidebarWidth(rightPanelWidth);
      layoutEl.style.setProperty("--left-sidebar-width", `${leftPanelWidth}px`);
      layoutEl.style.setProperty("--right-sidebar-width", `${rightPanelWidth}px`);
      layoutEl.classList.toggle("narrow-layout", narrow);
      layoutEl.classList.toggle("left-panel-collapsed", !narrow && leftPanelCollapsed);
      layoutEl.classList.toggle("right-panel-collapsed", !narrow && rightPanelCollapsed);
      layoutEl.classList.toggle("left-panel-mobile-open", narrow && leftPanelMobileOpen);
      layoutEl.classList.toggle("right-panel-mobile-open", narrow && rightPanelMobileOpen);

      const leftExpanded = narrow ? leftPanelMobileOpen : !leftPanelCollapsed;
      const rightExpanded = narrow ? rightPanelMobileOpen : !rightPanelCollapsed;
      leftPanelToggle.textContent = leftExpanded ? "⟨" : "⟩";
      panelToggle.textContent = rightExpanded ? "⟩" : "⟨";
      leftPanelToggle.setAttribute("aria-expanded", leftExpanded ? "true" : "false");
      panelToggle.setAttribute("aria-expanded", rightExpanded ? "true" : "false");
      leftPanelToggle.title = leftExpanded ? "Collapse selection inspector" : "Expand selection inspector";
      panelToggle.title = rightExpanded ? "Collapse graph controls" : "Expand graph controls";
      leftPanelResize.setAttribute("aria-valuemin", String(SIDEBAR_MIN_WIDTH));
      leftPanelResize.setAttribute("aria-valuemax", String(clampSidebarWidth(SIDEBAR_MAX_WIDTH)));
      leftPanelResize.setAttribute("aria-valuenow", String(leftPanelWidth));
      rightPanelResize.setAttribute("aria-valuemin", String(SIDEBAR_MIN_WIDTH));
      rightPanelResize.setAttribute("aria-valuemax", String(clampSidebarWidth(SIDEBAR_MAX_WIDTH)));
      rightPanelResize.setAttribute("aria-valuenow", String(rightPanelWidth));
    }

    leftPanelToggle.addEventListener("click", () => {
      if (isNarrowViewport()) {
        leftPanelMobileOpen = !leftPanelMobileOpen;
        if (leftPanelMobileOpen) rightPanelMobileOpen = false;
      } else {
        leftPanelCollapsed = !leftPanelCollapsed;
      }
      syncSidebarLayout();
      saveViewerState();
    });
    panelToggle.addEventListener("click", () => {
      if (isNarrowViewport()) {
        rightPanelMobileOpen = !rightPanelMobileOpen;
        if (rightPanelMobileOpen) leftPanelMobileOpen = false;
      } else {
        rightPanelCollapsed = !rightPanelCollapsed;
      }
      syncSidebarLayout();
      saveViewerState();
    });

    function bindSidebarResize(handle, side) {
      handle.addEventListener("pointerdown", event => {
        if (isNarrowViewport()) return;
        if ((side === "left" && leftPanelCollapsed) || (side === "right" && rightPanelCollapsed)) return;
        const startX = event.clientX;
        const startWidth = side === "left" ? leftPanelWidth : rightPanelWidth;
        const move = moveEvent => {
          const delta = moveEvent.clientX - startX;
          const nextWidth = clampSidebarWidth(startWidth + (side === "left" ? delta : -delta));
          if (side === "left") leftPanelWidth = nextWidth;
          else rightPanelWidth = nextWidth;
          syncSidebarLayout();
        };
        const finish = () => {
          document.removeEventListener("pointermove", move);
          document.removeEventListener("pointerup", finish);
          saveViewerState();
        };
        document.addEventListener("pointermove", move);
        document.addEventListener("pointerup", finish);
        event.preventDefault();
      });
      handle.addEventListener("keydown", event => {
        if (isNarrowViewport()) return;
        let delta = 0;
        if (event.key === "ArrowLeft") delta = side === "left" ? -10 : 10;
        else if (event.key === "ArrowRight") delta = side === "left" ? 10 : -10;
        else if (event.key === "Home") delta = SIDEBAR_MIN_WIDTH - (side === "left" ? leftPanelWidth : rightPanelWidth);
        else if (event.key === "End") delta = SIDEBAR_MAX_WIDTH;
        else return;
        if (side === "left") leftPanelWidth = clampSidebarWidth(leftPanelWidth + delta);
        else rightPanelWidth = clampSidebarWidth(rightPanelWidth + delta);
        syncSidebarLayout();
        saveViewerState();
        event.preventDefault();
      });
    }
    bindSidebarResize(leftPanelResize, "left");
    bindSidebarResize(rightPanelResize, "right");

    const advancedControlsSlot = document.getElementById("advanced-controls-slot");
    const routingControlsEl = document.getElementById("routing-controls");
    if (routingControlsEl) advancedControlsSlot.appendChild(routingControlsEl);
    ["cheatsheet", "raw-json"].forEach(sectionId => {
      const section = panelContent.querySelector(`[data-section-id="${sectionId}"]`);
      if (section) advancedControlsSlot.appendChild(section);
    });

    let wasNarrowViewport = isNarrowViewport();
    window.addEventListener("resize", () => {
      const narrow = isNarrowViewport();
      if (narrow && !wasNarrowViewport) {
        leftPanelMobileOpen = false;
        rightPanelMobileOpen = false;
      }
      wasNarrowViewport = narrow;
      syncSidebarLayout();
    });
