"use strict";

(function () {
  try {
    const storageKey = "softnix_mobile_v1";
    const raw = localStorage.getItem(storageKey);
    const parsed = raw ? JSON.parse(raw) : null;
    const mode = ["system", "light", "dark"].includes(parsed?.settings?.themeMode) ? parsed.settings.themeMode : "system";
    const isDark = mode === "dark" || (mode === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const theme = isDark ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.setAttribute("content", theme === "dark" ? "#0f1117" : "#2587c8");
  } catch (_) {}
})();
