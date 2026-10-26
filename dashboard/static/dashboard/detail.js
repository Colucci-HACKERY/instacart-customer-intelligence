/* Progressive feedback only: native GET forms and links remain authoritative. */
(() => {
    "use strict";
    const page = document.querySelector("body.theme-navy .detail-page");
    if (!page) return;

    page.querySelectorAll("form[method='get']").forEach(form => {
        const values = () => new URLSearchParams(new FormData(form)).toString();
        const initial = values();
        const feedback = document.createElement("p");
        feedback.className = "filter-feedback";
        feedback.setAttribute("role", "status");
        feedback.setAttribute("aria-live", "polite");
        feedback.hidden = true;
        form.insertAdjacentElement("afterend", feedback);

        const update = () => {
            const changed = values() !== initial;
            feedback.hidden = !changed;
            feedback.textContent = changed ? "Changes not applied. Use the action above to update results." : "";
        };
        form.addEventListener("input", update);
        form.addEventListener("change", update);
        form.addEventListener("submit", event => {
            if (event.defaultPrevented) return;
            form.setAttribute("aria-busy", "true");
            feedback.hidden = false;
            feedback.textContent = "Updating results…";
        });
        window.addEventListener("pageshow", () => {
            form.removeAttribute("aria-busy");
            update();
        });
    });

    const sections = [...page.querySelectorAll(".detail-nav a, .product-subnav a")]
        .map(link => {
            const target = document.getElementById(new URL(link.href, window.location.href).hash.slice(1));
            return target ? {link, target} : null;
        }).filter(Boolean);
    if (!sections.length || !("IntersectionObserver" in window)) return;

    const updateSection = () => {
        // Choose by position, not navigation order: records can precede directories.
        const ordered = [...sections].sort((a, b) => a.target.getBoundingClientRect().top - b.target.getBoundingClientRect().top);
        const passed = ordered.filter(section => section.target.getBoundingClientRect().top <= 120);
        const current = passed.at(-1) || ordered[0];
        sections.forEach(section => {
            const active = section === current;
            section.link.classList.toggle("is-current", active);
            if (active) section.link.setAttribute("aria-current", "location");
            else section.link.removeAttribute("aria-current");
        });
    };
    const observer = new window.IntersectionObserver(updateSection, {
        rootMargin: "-90px 0px -60% 0px", threshold: 0
    });
    sections.forEach(section => observer.observe(section.target));
    window.addEventListener("pageshow", updateSection);
    updateSection();
})();
