/*
 * ImmortalBrowser - a tiny in-app "browser" for navigating ImmortalNet
 * websites. It never runs the visited site's code in this page's origin:
 * it only ever points a sandboxed <iframe> at /site/<domain>, which the
 * server renders and locks down (see app.py view_site()).
 */
const ImmortalBrowser = (() => {
    let history = [];
    let historyIndex = -1;

    let addressBar, siteFrame, placeholder, btnBack, btnForward, btnReload;

    function extractDomain(input) {
        let value = input.trim().toLowerCase();
        if (!value) return "";
        value = value.replace(/^https?:\/\//, "");
        value = value.replace(/\/.*$/, "");
        value = value.replace(/\.immortalnet$/, "");
        return value;
    }

    function updateNavButtons() {
        btnBack.disabled = historyIndex <= 0;
        btnForward.disabled = historyIndex >= history.length - 1;
    }

    function loadDomain(domain, pushToHistory = true) {
        domain = extractDomain(domain);
        if (!domain) return;

        addressBar.value = `${domain}.immortalnet`;
        siteFrame.src = `/site/${encodeURIComponent(domain)}`;
        siteFrame.style.display = "block";
        placeholder.style.display = "none";

        if (pushToHistory) {
            // Drop any "forward" entries once the user navigates anew.
            history = history.slice(0, historyIndex + 1);
            history.push(domain);
            historyIndex = history.length - 1;
        }
        updateNavButtons();
    }

    function go() {
        loadDomain(addressBar.value);
    }

    function back() {
        if (historyIndex > 0) {
            historyIndex -= 1;
            loadDomain(history[historyIndex], false);
        }
    }

    function forward() {
        if (historyIndex < history.length - 1) {
            historyIndex += 1;
            loadDomain(history[historyIndex], false);
        }
    }

    function reload() {
        if (historyIndex >= 0) {
            // Force a real reload even if src is unchanged.
            const domain = history[historyIndex];
            siteFrame.src = `/site/${encodeURIComponent(domain)}?_=${Date.now()}`;
        }
    }

    function init(initialDomain) {
        addressBar = document.getElementById("address-bar");
        siteFrame = document.getElementById("site-frame");
        placeholder = document.getElementById("browser-placeholder");
        btnBack = document.getElementById("btn-back");
        btnForward = document.getElementById("btn-forward");
        btnReload = document.getElementById("btn-reload");

        siteFrame.style.display = "none";

        btnBack.addEventListener("click", back);
        btnForward.addEventListener("click", forward);
        btnReload.addEventListener("click", reload);
        document.getElementById("btn-go").addEventListener("click", go);
        addressBar.addEventListener("keydown", (e) => {
            if (e.key === "Enter") go();
        });

        updateNavButtons();

        if (initialDomain) {
            loadDomain(initialDomain);
        }
    }

    return { init };
})();
