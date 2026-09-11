(function () {
  var theme = "";

  try {
    theme = localStorage.getItem("theme") || "";
  } catch (error) {
    theme = "";
  }

  if (!theme) {
    var match = document.cookie.match(/(?:^|; )theme=(dark|light)(?:;|$)/);
    theme = match ? match[1] : "";
  }

  if (!theme) {
    var systemDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    theme = systemDark ? "dark" : "light";
  }

  document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
})();
