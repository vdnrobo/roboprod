(function () {
  const EXIT_CLASS = "page-transition-exit";
  const ENTER_CLASS = "page-transition-enter";
  const ENTER_ACTIVE_CLASS = "page-transition-enter-active";
  const READY_CLASS = "page-transition-ready";
  const EXIT_DURATION_MS = 150;
  const ENTER_CLEANUP_MS = 220;
  const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  const shouldAnimate = () => !reducedMotionQuery.matches;

  const cleanupEnter = () => {
    document.body.classList.remove(ENTER_CLASS, ENTER_ACTIVE_CLASS);
  };

  const runEnter = () => {
    if (!shouldAnimate()) {
      return;
    }
    document.documentElement.classList.add(READY_CLASS);
    document.body.classList.remove(EXIT_CLASS);
    document.body.classList.add(ENTER_CLASS);
    window.requestAnimationFrame(() => {
      document.body.classList.add(ENTER_ACTIVE_CLASS);
      window.setTimeout(cleanupEnter, ENTER_CLEANUP_MS);
    });
  };

  const isDownloadPath = (url) => {
    const path = url.pathname;
    return (
      path.endsWith("/file/")
      || path.endsWith("/export/")
      || path.includes("/bulk/files/")
      || path.includes("/previews/")
      || path.endsWith("/source-file/")
    );
  };

  const canTransitionLink = (event, link) => {
    if (!shouldAnimate() || event.defaultPrevented || event.button !== 0) {
      return false;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return false;
    }
    if (link.closest("form") || link.hasAttribute("download")) {
      return false;
    }
    if (link.target && link.target !== "_self") {
      return false;
    }

    const href = link.getAttribute("href") || "";
    if (!href || href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("tel:")) {
      return false;
    }

    let url;
    try {
      url = new URL(href, window.location.href);
    } catch (_error) {
      return false;
    }

    if (url.origin !== window.location.origin || !["http:", "https:"].includes(url.protocol)) {
      return false;
    }
    if (url.pathname === window.location.pathname && url.search === window.location.search) {
      return false;
    }
    return !isDownloadPath(url);
  };

  const navigate = (target) => {
    if (!shouldAnimate()) {
      window.location.href = target;
      return;
    }
    document.documentElement.classList.add(READY_CLASS);
    document.body.classList.remove(ENTER_CLASS, ENTER_ACTIVE_CLASS);
    document.body.classList.add(EXIT_CLASS);
    window.setTimeout(() => {
      window.location.href = target;
    }, EXIT_DURATION_MS);
  };

  const handleClick = (event) => {
    const link = event.target.closest("a[href]");
    if (!link || !canTransitionLink(event, link)) {
      return;
    }
    event.preventDefault();
    navigate(link.href);
  };

  window.RoboPageTransitions = { navigate };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", runEnter);
  } else {
    runEnter();
  }
  document.addEventListener("click", handleClick);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      document.body.classList.remove(EXIT_CLASS);
      cleanupEnter();
    }
  });
})();
