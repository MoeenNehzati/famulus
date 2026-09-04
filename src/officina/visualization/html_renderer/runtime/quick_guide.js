const quickGuideToolbarItem = document.getElementById("quick-guide-toolbar-item");
const quickGuideButton = document.getElementById("quick-guide-btn");
const quickGuideHighlight = document.getElementById("quick-guide-highlight");
const quickGuideDialog = document.getElementById("quick-guide-dialog");
const quickGuideTitle = document.getElementById("quick-guide-title");
const quickGuideStep = document.getElementById("quick-guide-step");
const quickGuideBody = document.getElementById("quick-guide-body");
const quickGuideBack = document.getElementById("quick-guide-back");
const quickGuideNext = document.getElementById("quick-guide-next");
const quickGuideFinish = document.getElementById("quick-guide-finish");
const quickGuideClose = document.getElementById("quick-guide-close");

let quickGuideSteps = [];
let quickGuideIndex = -1;
let quickGuideReturnFocus = null;
let quickGuideTargetObserver = null;
let quickGuideHealthCheckPending = false;

function scheduleQuickGuideHealthCheck() {
  if (quickGuideHealthCheckPending || !isQuickGuideOpen()) return;
  quickGuideHealthCheckPending = true;
  setTimeout(() => {
    quickGuideHealthCheckPending = false;
    positionQuickGuide();
  }, 0);
}

function isQuickGuideOpen() {
  return !quickGuideDialog.hidden && quickGuideIndex >= 0;
}

function quickGuideOwnsFocus() {
  return isQuickGuideOpen() && quickGuideDialog.contains(document.activeElement);
}

function resolveQuickGuideTarget(step) {
  if (!step || typeof step.target !== "string") return null;
  try {
    const target = document.querySelector(step.target);
    if (!target) return null;
    const rect = target.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    if (!target.getClientRects().length) return null;
    return {target, rect};
  } catch (error) {
    return null;
  }
}

function findQuickGuideStep(startIndex, direction) {
  let index = startIndex;
  while (index >= 0 && index < quickGuideSteps.length) {
    if (resolveQuickGuideTarget(quickGuideSteps[index])) return index;
    index += direction;
  }
  return -1;
}

function renderQuickGuideStep() {
  if (!isQuickGuideOpen()) return;

  let step = quickGuideSteps[quickGuideIndex];
  let match = resolveQuickGuideTarget(step);
  if (!match) {
    const forwardIndex = findQuickGuideStep(quickGuideIndex + 1, +1);
    if (forwardIndex >= 0) {
      quickGuideIndex = forwardIndex;
    } else {
      const backwardIndex = findQuickGuideStep(quickGuideIndex - 1, -1);
      if (backwardIndex >= 0) quickGuideIndex = backwardIndex;
      else {
        closeQuickGuide();
        return;
      }
    }
    step = quickGuideSteps[quickGuideIndex];
    match = resolveQuickGuideTarget(step);
    if (!match) {
      closeQuickGuide();
      return;
    }
  }

  const usableSteps = [];
  for (let index = 0; index < quickGuideSteps.length; index += 1) {
    if (resolveQuickGuideTarget(quickGuideSteps[index])) usableSteps.push(index);
  }

  const stepPosition = usableSteps.indexOf(quickGuideIndex);
  const stepCount = usableSteps.length;
  if (stepCount === 0 || stepPosition < 0) {
    closeQuickGuide();
    return;
  }

  quickGuideDialog.hidden = false;
  quickGuideHighlight.hidden = false;
  quickGuideTitle.textContent = step.title || "";
  quickGuideBody.textContent = step.body || "";
  quickGuideStep.textContent = `${stepPosition + 1} of ${stepCount}`;
  quickGuideBack.hidden = stepPosition === 0;
  quickGuideNext.hidden = stepPosition === stepCount - 1;
  quickGuideFinish.hidden = !quickGuideNext.hidden;

  if (!quickGuideNext.hidden) {
    quickGuideNext.focus();
  } else {
    quickGuideFinish.focus();
  }
  positionQuickGuide();
}

function positionQuickGuide() {
  if (!isQuickGuideOpen()) return;

  const step = quickGuideSteps[quickGuideIndex];
  const match = resolveQuickGuideTarget(step);
  if (!match) {
    // The active step's target is gone; renderQuickGuideStep() carries the
    // same forward/backward search plus the title/body/count sync this
    // function does not, so recovery never leaves the dialog showing a step
    // that no longer matches the highlighted target.
    renderQuickGuideStep();
    return;
  }

  const targetRect = match.rect;
  quickGuideDialog.style.width = `${Math.min(360, Math.max(0, window.innerWidth - 16))}px`;
  quickGuideDialog.style.maxWidth = `calc(100vw - 16px)`;
  quickGuideDialog.hidden = false;
  const dialogRect = quickGuideDialog.getBoundingClientRect();
  const dialogHeight = dialogRect.height || 0;
  const dialogWidth = Math.min(360, Math.max(0, window.innerWidth - 16));

  const x = Math.max(8, Math.min(
    targetRect.left,
    window.innerWidth - dialogWidth - 8,
  ));
  const belowY = targetRect.bottom + 8;
  const aboveY = targetRect.top - dialogHeight - 8;
  let y = belowY + dialogHeight <= window.innerHeight - 8 ? belowY : aboveY;
  y = Math.max(8, Math.min(y, window.innerHeight - dialogHeight - 8));

  quickGuideHighlight.style.left = `${targetRect.left}px`;
  quickGuideHighlight.style.top = `${targetRect.top}px`;
  quickGuideHighlight.style.width = `${targetRect.width}px`;
  quickGuideHighlight.style.height = `${targetRect.height}px`;
  quickGuideDialog.style.left = `${x}px`;
  quickGuideDialog.style.top = `${y}px`;
}

function startQuickGuide() {
  quickGuideSteps = Array.isArray(QUICK_GUIDE_CONFIG?.steps)
    ? [...QUICK_GUIDE_CONFIG.steps]
    : [];
  quickGuideReturnFocus = document.activeElement;
  quickGuideIndex = findQuickGuideStep(0, +1);
  if (quickGuideIndex < 0) {
    closeQuickGuide();
    return;
  }

  quickGuideDialog.hidden = false;
  quickGuideHighlight.hidden = false;
  renderQuickGuideStep();

  // A step's target can disappear (hidden, dimmed, removed) from actions
  // taken outside the guide itself; watch for that so the dialog moves off
  // it without waiting for a resize/scroll to trigger positionQuickGuide().
  quickGuideTargetObserver = new MutationObserver(scheduleQuickGuideHealthCheck);
  quickGuideTargetObserver.observe(document.body, {
    attributes: true,
    attributeFilter: ["style", "class", "hidden"],
    childList: true,
    subtree: true,
  });
}

function closeQuickGuide() {
  const returnFocus = quickGuideReturnFocus;
  quickGuideSteps = [];
  quickGuideIndex = -1;
  quickGuideReturnFocus = null;
  quickGuideDialog.hidden = true;
  quickGuideHighlight.hidden = true;
  if (quickGuideTargetObserver) {
    quickGuideTargetObserver.disconnect();
    quickGuideTargetObserver = null;
  }
  if (returnFocus?.isConnected && returnFocus.focus) returnFocus.focus();
}

function nextQuickGuideStep() {
  const nextIndex = findQuickGuideStep(quickGuideIndex + 1, +1);
  if (nextIndex < 0) return;
  quickGuideIndex = nextIndex;
  renderQuickGuideStep();
}

function previousQuickGuideStep() {
  const previousIndex = findQuickGuideStep(quickGuideIndex - 1, -1);
  if (previousIndex < 0) return;
  quickGuideIndex = previousIndex;
  renderQuickGuideStep();
}

function hasUsableQuickGuideTarget() {
  const configuredSteps = Array.isArray(QUICK_GUIDE_CONFIG?.steps)
    ? QUICK_GUIDE_CONFIG.steps
    : [];
  return configuredSteps.some(step => resolveQuickGuideTarget(step));
}

quickGuideBack.addEventListener("click", previousQuickGuideStep);
quickGuideNext.addEventListener("click", nextQuickGuideStep);
quickGuideFinish.addEventListener("click", closeQuickGuide);
quickGuideClose.addEventListener("click", closeQuickGuide);
quickGuideButton.addEventListener("click", startQuickGuide);
quickGuideToolbarItem.hidden = !hasUsableQuickGuideTarget();

document.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !isQuickGuideOpen()) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  closeQuickGuide();
}, true);

window.addEventListener("resize", () => {
  if (isQuickGuideOpen()) positionQuickGuide();
});

window.addEventListener("scroll", () => {
  if (isQuickGuideOpen()) positionQuickGuide();
}, true);
