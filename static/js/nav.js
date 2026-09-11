document.addEventListener("DOMContentLoaded", () => {
  const navSelect = document.querySelector(".mobile-nav-select");
  if (navSelect) {
    navSelect.addEventListener("change", () => {
      const target = navSelect.value;
      if (target && target !== window.location.pathname) {
        if (window.RoboPageTransitions) {
          window.RoboPageTransitions.navigate(target);
        } else {
          window.location.href = target;
        }
      }
    });
  }

  const collapsibles = Array.from(document.querySelectorAll("[data-collapsible-key]"));
  const mobileCollapsibleQuery = window.matchMedia("(max-width: 820px)");
  const applyCollapsibleState = (details) => {
    if (!mobileCollapsibleQuery.matches) {
      details.open = true;
      return;
    }

    const key = `collapsible:${details.dataset.collapsibleKey}`;
    try {
      const savedState = localStorage.getItem(key);
      if (savedState === "open") {
        details.open = true;
      } else if (savedState === "closed") {
        details.open = false;
      }
    } catch (error) {
      // localStorage may be unavailable in strict browser modes.
    }
  };

  collapsibles.forEach((details) => {
    const key = `collapsible:${details.dataset.collapsibleKey}`;
    applyCollapsibleState(details);

    details.addEventListener("toggle", () => {
      if (!mobileCollapsibleQuery.matches) {
        if (!details.open) {
          details.open = true;
        }
        return;
      }

      const nextState = details.open;
      collapsibles.forEach((other) => {
        if (other !== details && other.dataset.collapsibleKey === details.dataset.collapsibleKey) {
          other.open = nextState;
        }
      });
      try {
        localStorage.setItem(key, nextState ? "open" : "closed");
      } catch (error) {
        // localStorage may be unavailable in strict browser modes.
      }
    });
  });

  mobileCollapsibleQuery.addEventListener("change", () => {
    collapsibles.forEach((details) => applyCollapsibleState(details));
  });

  const themeButtons = Array.from(document.querySelectorAll("[data-theme-toggle]"));
  const getSavedTheme = () => {
    try {
      const savedTheme = localStorage.getItem("theme");
      if (savedTheme === "dark" || savedTheme === "light") {
        return savedTheme;
      }
    } catch (error) {
      // localStorage may be unavailable in strict browser modes.
    }

    const cookieTheme = document.cookie.match(/(?:^|; )theme=(dark|light)(?:;|$)/);
    return cookieTheme ? cookieTheme[1] : "";
  };

  const setTheme = (theme) => {
    const normalizedTheme = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = normalizedTheme;
    try {
      localStorage.setItem("theme", normalizedTheme);
    } catch (error) {
      // localStorage may be unavailable in strict browser modes.
    }
    document.cookie = `theme=${normalizedTheme}; path=/; max-age=31536000; samesite=lax`;
    themeButtons.forEach((button) => {
      button.textContent = normalizedTheme === "dark" ? "Светлая тема" : "Темная тема";
    });
  };

  const currentTheme = document.documentElement.dataset.theme || getSavedTheme() || "light";
  setTheme(currentTheme);
  themeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
  });

  const selectAllControls = Array.from(document.querySelectorAll("[data-select-all]"));
  const getSelectItems = (group) => Array.from(document.querySelectorAll(`[data-select-item="${group}"]`));
  const syncSelectAllState = (group) => {
    const items = getSelectItems(group);
    const enabledItems = items.filter((item) => !item.disabled);
    const checkedCount = enabledItems.filter((item) => item.checked).length;
    selectAllControls
      .filter((control) => control.dataset.selectAll === group)
      .forEach((control) => {
        control.checked = enabledItems.length > 0 && checkedCount === enabledItems.length;
        control.indeterminate = checkedCount > 0 && checkedCount < enabledItems.length;
        control.disabled = enabledItems.length === 0;
      });
  };

  selectAllControls.forEach((control) => {
    const group = control.dataset.selectAll;
    syncSelectAllState(group);
    control.addEventListener("change", () => {
      getSelectItems(group).forEach((item) => {
        if (!item.disabled) {
          item.checked = control.checked;
        }
      });
      syncSelectAllState(group);
    });
  });

  document.addEventListener("change", (event) => {
    const item = event.target.closest("[data-select-item]");
    if (item) {
      syncSelectAllState(item.dataset.selectItem);
    }
  });
});
