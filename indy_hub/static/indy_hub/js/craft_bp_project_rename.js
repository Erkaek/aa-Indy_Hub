/* global document, fetch, window */
(function () {
    "use strict";

    function getCsrfToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    function findContainer(target) {
        return target ? target.closest(".project-name-edit") : null;
    }

    function getParts(container) {
        if (!container) return null;
        return {
            container,
            button: container.querySelector(".project-name-edit-btn"),
            form: container.querySelector(".project-name-edit-form"),
            input: container.querySelector(".project-name-edit-input"),
            save: container.querySelector(".project-name-edit-save"),
            cancel: container.querySelector(".project-name-edit-cancel"),
            status: container.querySelector(".project-name-edit-status"),
        };
    }

    function getTitleEl() {
        return document.querySelector(".page-header-title-text");
    }

    function setEditing(parts, editing) {
        if (!parts) return;
        if (parts.form) {
            parts.form.classList.toggle("d-none", !editing);
            parts.form.classList.toggle("d-inline-flex", editing);
        }
        if (parts.button) {
            parts.button.classList.toggle("d-none", editing);
        }
        if (editing && parts.input) {
            parts.input.focus();
            parts.input.select();
        }
        if (!editing && parts.status) {
            parts.status.classList.add("d-none");
            parts.status.textContent = "";
        }
    }

    function showError(parts, message) {
        if (!parts || !parts.status) return;
        parts.status.textContent = message || "";
        parts.status.classList.toggle("d-none", !message);
    }

    function updateTitle(newName) {
        const titleEl = getTitleEl();
        if (titleEl) {
            titleEl.textContent = newName;
        }
        const pageTitle = document.title || "";
        const dashIdx = pageTitle.indexOf(" - ");
        if (dashIdx !== -1) {
            document.title = newName + pageTitle.substring(dashIdx);
        }
        const simulationNameInput = document.getElementById("simulationName");
        if (simulationNameInput) {
            simulationNameInput.value = newName;
        }
        if (window.BLUEPRINT_DATA) {
            window.BLUEPRINT_DATA.name = newName;
            if (window.BLUEPRINT_DATA.workspace_state) {
                window.BLUEPRINT_DATA.workspace_state.simulation_name = newName;
                window.BLUEPRINT_DATA.workspace_state.simulationName = newName;
            }
        }
    }

    async function submitRename(parts) {
        if (!parts || !parts.input) return;
        const url = parts.container.getAttribute("data-rename-url");
        const newName = String(parts.input.value || "").trim();
        if (!url) {
            showError(parts, "Rename endpoint unavailable.");
            return;
        }
        if (!newName) {
            showError(parts, parts.input.getAttribute("data-empty-message")
                || "Name must not be empty.");
            return;
        }
        if (parts.save) parts.save.disabled = true;
        if (parts.cancel) parts.cancel.disabled = true;
        try {
            const response = await fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken() || "",
                    Accept: "application/json",
                },
                body: JSON.stringify({ name: newName }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.success) {
                showError(parts, payload.error || "Failed to rename project.");
                return;
            }
            updateTitle(payload.name || newName);
            parts.input.value = payload.name || newName;
            setEditing(parts, false);
        } catch (error) {
            showError(parts, "Network error while renaming.");
        } finally {
            if (parts.save) parts.save.disabled = false;
            if (parts.cancel) parts.cancel.disabled = false;
        }
    }

    document.addEventListener("click", function (event) {
        const target = event.target;
        if (!target) return;

        const editBtn = target.closest(".project-name-edit-btn");
        if (editBtn) {
            event.preventDefault();
            const parts = getParts(findContainer(editBtn));
            if (parts) {
                if (parts.input) {
                    const current = getTitleEl();
                    if (current) parts.input.value = current.textContent.trim();
                }
                setEditing(parts, true);
            }
            return;
        }

        const cancelBtn = target.closest(".project-name-edit-cancel");
        if (cancelBtn) {
            event.preventDefault();
            setEditing(getParts(findContainer(cancelBtn)), false);
            return;
        }

        const saveBtn = target.closest(".project-name-edit-save");
        if (saveBtn) {
            event.preventDefault();
            submitRename(getParts(findContainer(saveBtn)));
        }
    });

    document.addEventListener("keydown", function (event) {
        const target = event.target;
        if (!target || !target.classList || !target.classList.contains("project-name-edit-input")) {
            return;
        }
        if (event.key === "Enter") {
            event.preventDefault();
            submitRename(getParts(findContainer(target)));
        } else if (event.key === "Escape") {
            event.preventDefault();
            setEditing(getParts(findContainer(target)), false);
        }
    });

    // --- Project status dropdown -------------------------------------------------

    const STATUS_ICONS = {
        draft: "fas fa-clock",
        saved: "fas fa-bookmark",
        archived: "fas fa-box-archive",
    };

    function updateStatusUi(dropdown, statusValue, statusLabel) {
        if (!dropdown) return;
        dropdown.setAttribute("data-current-status", statusValue);
        const labelEl = dropdown.querySelector(".project-status-label");
        if (labelEl && statusLabel) {
            labelEl.textContent = statusLabel;
        }
        const iconEl = dropdown.querySelector(".project-status-icon");
        if (iconEl) {
            const iconClass = STATUS_ICONS[statusValue] || "fas fa-circle";
            iconEl.className = iconClass + " me-1 project-status-icon";
        }
        dropdown.querySelectorAll(".project-status-option").forEach((opt) => {
            opt.classList.toggle(
                "active",
                opt.getAttribute("data-status") === statusValue
            );
        });
    }

    async function submitStatusChange(option) {
        const dropdown = option.closest(".project-status-dropdown");
        if (!dropdown) return;
        const url = dropdown.getAttribute("data-status-url");
        const newStatus = option.getAttribute("data-status");
        const currentStatus = dropdown.getAttribute("data-current-status");
        if (!url || !newStatus || newStatus === currentStatus) {
            return;
        }
        option.classList.add("disabled");
        try {
            const response = await fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken() || "",
                    Accept: "application/json",
                },
                body: JSON.stringify({ status: newStatus }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.success) {
                window.alert(payload.error || "Failed to update project status.");
                return;
            }
            updateStatusUi(dropdown, payload.status || newStatus, payload.status_label || option.textContent.trim());
        } catch (error) {
            window.alert("Network error while updating project status.");
        } finally {
            option.classList.remove("disabled");
        }
    }

    document.addEventListener("click", function (event) {
        const option = event.target.closest(".project-status-option");
        if (!option) return;
        event.preventDefault();
        submitStatusChange(option);
    });
})();
