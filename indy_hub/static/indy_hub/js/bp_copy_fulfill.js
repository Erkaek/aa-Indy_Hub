(function () {
    "use strict";

    var debugEnabled = Boolean(window.INDY_HUB_DEBUG === true);

    function debugWarn() {
        if (!debugEnabled) {
            return;
        }
        if (window.console && typeof window.console.warn === "function") {
            window.console.warn.apply(window.console, arguments);
        }
    }

    function __(text) {
        if (typeof window.gettext === "function") {
            return window.gettext(text);
        }
        return text;
    }

    function fallbackCopy(value) {
        return new Promise(function (resolve, reject) {
            var textarea = document.createElement("textarea");
            textarea.value = value;
            textarea.setAttribute("readonly", "");
            textarea.style.position = "absolute";
            textarea.style.left = "-9999px";
            document.body.appendChild(textarea);
            textarea.select();
            textarea.setSelectionRange(0, textarea.value.length);
            try {
                var successful = document.execCommand("copy");
                document.body.removeChild(textarea);
                if (successful) {
                    resolve();
                } else {
                    reject(new Error("execCommand returned false"));
                }
            } catch (err) {
                document.body.removeChild(textarea);
                reject(err);
            }
        });
    }

    function copyToClipboard(value) {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
            return navigator.clipboard.writeText(value);
        }
        return fallbackCopy(value);
    }

    function resetFeedback(button) {
        var icon = button.querySelector("i");
        var originalIcon = icon ? icon.getAttribute("data-original-class") : null;
        if (icon && originalIcon) {
            icon.className = originalIcon;
        }
        var originalLabel = button.getAttribute("data-original-aria-label");
        if (originalLabel) {
            button.setAttribute("aria-label", originalLabel);
        }
        button.classList.remove("is-copy-feedback");
        button.classList.remove("is-copy-error");
    }

    function showFeedback(button, isError) {
        var icon = button.querySelector("i");
        if (icon && !icon.getAttribute("data-original-class")) {
            icon.setAttribute("data-original-class", icon.className);
        }
        if (!button.getAttribute("data-original-aria-label")) {
            button.setAttribute(
                "data-original-aria-label",
                button.getAttribute("aria-label") || ""
            );
        }

        var successLabel = button.getAttribute("data-copy-success-label") || __("Copied!");
        var errorLabel = button.getAttribute("data-copy-error-label") || __("Unable to copy");
        var newLabel = isError ? errorLabel : successLabel;

        if (icon) {
            icon.className = isError ? "fas fa-exclamation-triangle" : "fas fa-check";
        }
        button.setAttribute("aria-label", newLabel);
        button.classList.add("is-copy-feedback");
        button.classList.toggle("is-copy-error", Boolean(isError));

        window.setTimeout(function () {
            resetFeedback(button);
        }, 1800);
    }

    function handleCopyClick(event) {
        event.preventDefault();
        var button = event.currentTarget;
        var value = button.getAttribute("data-copy-value");
        if (!value) {
            return;
        }

        copyToClipboard(value)
            .then(function () {
                showFeedback(button, false);
            })
            .catch(function () {
                fallbackCopy(value)
                    .then(function () {
                        showFeedback(button, false);
                    })
                    .catch(function () {
                        showFeedback(button, true);
                    });
            });
    }

    function initCopyButtons() {
        var buttons = document.querySelectorAll(
            ".bp-request-card__copy-button[data-copy-value]"
        );
        if (!buttons.length) {
            return;
        }
        buttons.forEach(function (button) {
            button.addEventListener("click", handleCopyClick);
        });
    }

    var scopeDataCache = Object.create(null);
    var scopeSelections = Object.create(null);

    function revealCollapseSection(collapseId) {
        if (!collapseId) {
            return;
        }
        var collapseEl = document.getElementById(collapseId);
        if (!collapseEl) {
            return;
        }
        var collapseCtor = null;
        if (typeof window !== "undefined") {
            if (window.bootstrap && window.bootstrap.Collapse) {
                collapseCtor = window.bootstrap.Collapse;
            } else if (window.bootstrap5 && window.bootstrap5.Collapse) {
                collapseCtor = window.bootstrap5.Collapse;
            }
        }
        var instance = null;
        if (collapseCtor) {
            if (typeof collapseCtor.getOrCreateInstance === "function") {
                instance = collapseCtor.getOrCreateInstance(collapseEl, { toggle: false });
            } else {
                instance = new collapseCtor(collapseEl, { toggle: false });
            }
            if (instance && typeof instance.show === "function") {
                instance.show();
            }
        } else {
            collapseEl.classList.add("show");
            collapseEl.style.height = "auto";
        }
        window.setTimeout(function () {
            var focusTarget = collapseEl.querySelector("textarea, input, select, button");
            if (!focusTarget) {
                return;
            }
            try {
                focusTarget.focus({ preventScroll: true });
            } catch (err) {
                focusTarget.focus();
            }
        }, 75);
    }

    function parseScopeData(scriptId) {
        if (!scriptId) {
            return null;
        }
        if (scopeDataCache[scriptId]) {
            return scopeDataCache[scriptId];
        }
        var script = document.getElementById(scriptId);
        if (!script) {
            return null;
        }
        var raw = script.textContent || script.innerText || "";
        if (!raw) {
            return null;
        }
        try {
            var data = JSON.parse(raw);
            scopeDataCache[scriptId] = data;
            return data;
        } catch (err) {
            debugWarn("[IndyHub] Failed to parse scope options", err);
            return null;
        }
    }

    function highlightSelected(container) {
        if (!container) {
            return;
        }
        var nodes = container.querySelectorAll("[data-scope-option]");
        nodes.forEach(function (node) {
            var input = node.querySelector('input[type="radio"]');
            if (input && input.checked) {
                node.classList.add("is-selected");
            } else {
                node.classList.remove("is-selected");
            }
        });
    }

    function initScopeSelector() {
        var triggers = document.querySelectorAll("[data-scope-trigger]");
        if (!triggers.length) {
            return;
        }

        var modalEl = document.getElementById("bpScopeSelectModal");
        var titleEl = modalEl ? modalEl.querySelector("[data-scope-title]") : null;
        var helperEl = modalEl ? modalEl.querySelector("[data-scope-helper]") : null;
        var optionsContainer = modalEl ? modalEl.querySelector("[data-scope-options]") : null;
        var warningEl = modalEl ? modalEl.querySelector("[data-scope-warning]") : null;
        var confirmBtn = modalEl ? modalEl.querySelector("[data-scope-confirm]") : null;
        var cancelBtn = modalEl ? modalEl.querySelector("[data-scope-cancel]") : null;

        var labelCharacter = "Character";
        var labelCorporation = "Corporation";
        var badgeYou = "You";
        var badgeAccess = "Your access";
        var warningMessage = "Please select an option before continuing.";
        var headingCharacters = "Characters";
        var headingCorporations = "Corporations";

        if (optionsContainer) {
            labelCharacter = optionsContainer.getAttribute("data-label-character") || labelCharacter;
            labelCorporation = optionsContainer.getAttribute("data-label-corporation") || labelCorporation;
            badgeYou = optionsContainer.getAttribute("data-badge-you") || badgeYou;
            badgeAccess = optionsContainer.getAttribute("data-badge-access") || badgeAccess;
            warningMessage = optionsContainer.getAttribute("data-warning-message") || warningMessage;
            headingCharacters = optionsContainer.getAttribute("data-heading-characters") || headingCharacters;
            headingCorporations = optionsContainer.getAttribute("data-heading-corporations") || headingCorporations;
        }

        var bootstrapModalCtor = null;
        if (typeof window !== "undefined") {
            if (window.bootstrap && window.bootstrap.Modal) {
                bootstrapModalCtor = window.bootstrap.Modal;
            } else if (window.bootstrap5 && window.bootstrap5.Modal) {
                bootstrapModalCtor = window.bootstrap5.Modal;
            }
        }

        var modalController = null;
        if (modalEl && bootstrapModalCtor) {
            modalController = (function () {
                var instance = null;
                function ensureInstance() {
                    if (instance) {
                        return instance;
                    }
                    if (typeof bootstrapModalCtor.getOrCreateInstance === "function") {
                        instance = bootstrapModalCtor.getOrCreateInstance(modalEl);
                    } else {
                        instance = new bootstrapModalCtor(modalEl);
                    }
                    return instance;
                }
                return {
                    show: function () {
                        ensureInstance().show();
                    },
                    hide: function () {
                        if (!instance) {
                            return;
                        }
                        if (typeof instance.hide === "function") {
                            instance.hide();
                        }
                    },
                };
            })();
        }

        var useModal = Boolean(modalController);
        var currentContext = null;

        function resetModalState() {
            if (!optionsContainer) {
                return;
            }
            optionsContainer.innerHTML = "";
            if (warningEl) {
                warningEl.textContent = "";
                warningEl.classList.add("d-none");
            }
            if (confirmBtn) {
                confirmBtn.disabled = true;
            }
            if (helperEl) {
                var defaultHelper = helperEl.getAttribute("data-default-helper") || "";
                helperEl.textContent = defaultHelper;
            }
            if (titleEl) {
                var defaultTitle = titleEl.getAttribute("data-default-title") || "";
                titleEl.textContent = defaultTitle;
            }
        }

        if (useModal && modalEl) {
            modalEl.addEventListener("hidden.bs.modal", function () {
                resetModalState();
                currentContext = null;
            });
            modalEl.addEventListener("shown.bs.modal", function () {
                if (!optionsContainer) {
                    return;
                }
                var firstInput = optionsContainer.querySelector('input[type="radio"]');
                if (firstInput) {
                    try {
                        firstInput.focus({ preventScroll: true });
                    } catch (err) {
                        firstInput.focus();
                    }
                }
            });
        }

        if (cancelBtn && useModal && !cancelBtn.hasAttribute("data-scope-handler")) {
            cancelBtn.setAttribute("data-scope-handler", "true");
            cancelBtn.addEventListener("click", function () {
                if (modalController) {
                    modalController.hide();
                }
            });
        }

        function updateFormsForSelection(requestId, selection) {
            var inputs = document.querySelectorAll('[data-scope-input][data-request-id="' + requestId + '"]');
            inputs.forEach(function (input) {
                input.value = selection.scope || "";
            });

            var displays = document.querySelectorAll('[data-scope-display][data-request-id="' + requestId + '"]');
            var summaryParts = [];
            if (selection.kindLabel) {
                summaryParts.push(selection.kindLabel);
            }
            if (selection.label) {
                summaryParts.push(selection.label);
            }
            var summaryText = summaryParts.join(" - ");
            displays.forEach(function (display) {
                if (!summaryText) {
                    display.textContent = "";
                    display.classList.add("d-none");
                } else {
                    display.textContent = summaryText;
                    display.classList.remove("d-none");
                }
            });
        }

        function submitSelection(context, selection) {
            if (!context) {
                return;
            }
            scopeSelections[context.requestId] = selection;
            updateFormsForSelection(context.requestId, selection);
            if (useModal && modalController) {
                modalController.hide();
            }
            currentContext = null;
            var collapseId = context.openCollapseId;
            if (collapseId) {
                if (context.trigger) {
                    context.trigger.setAttribute("aria-expanded", "true");
                }
                revealCollapseSection(collapseId);
                return;
            }
            var form = context.form;
            if (!form) {
                return;
            }
            if (typeof form.requestSubmit === "function") {
                form.requestSubmit();
            } else {
                form.submit();
            }
        }

        if (confirmBtn && useModal && !confirmBtn.hasAttribute("data-scope-handler")) {
            confirmBtn.setAttribute("data-scope-handler", "true");
            confirmBtn.addEventListener("click", function () {
                if (!currentContext || !optionsContainer) {
                    return;
                }
                var selected = optionsContainer.querySelector('input[name="scopeSelection"]:checked');
                if (!selected) {
                    if (warningEl) {
                        warningEl.textContent = warningMessage;
                        warningEl.classList.remove("d-none");
                    }
                    return;
                }
                if (warningEl) {
                    warningEl.classList.add("d-none");
                }
                var selection = {
                    scope: selected.value || "",
                    label: selected.getAttribute("data-option-label") || "",
                    kind: selected.getAttribute("data-option-kind") || "",
                    kindLabel: selected.getAttribute("data-option-kind-label") || "",
                };
                submitSelection(currentContext, selection);
            });
        }

        function promptSelection(data) {
            if (!data) {
                return null;
            }
            var options = [];
            if (Array.isArray(data.characters) && data.characters.length) {
                var characterName = data.characters[0].name || labelCharacter;
                var moreCharacters = data.characters.length > 1 ? " (+" + (data.characters.length - 1) + ")" : "";
                options.push("1 - " + labelCharacter + ": " + characterName + moreCharacters);
            }
            if (Array.isArray(data.corporations) && data.corporations.length) {
                var corporationName = data.corporations[0].name || labelCorporation;
                var moreCorps = data.corporations.length > 1 ? " (+" + (data.corporations.length - 1) + ")" : "";
                options.push("2 - " + labelCorporation + ": " + corporationName + moreCorps);
            }
            if (!options.length) {
                return null;
            }
            var promptMessage = "Select a fulfilment source:\n" + options.join("\n");
            var response = window.prompt(promptMessage, "");
            if (!response) {
                return null;
            }
            var trimmed = response.trim();
            if (trimmed === "1" && Array.isArray(data.characters) && data.characters.length) {
                return {
                    scope: "personal",
                    label: data.characters[0].name || "",
                    kind: "character",
                    kindLabel: labelCharacter,
                };
            }
            if (trimmed === "2" && Array.isArray(data.corporations) && data.corporations.length) {
                return {
                    scope: "corporation",
                    label: data.corporations[0].name || "",
                    kind: "corporation",
                    kindLabel: labelCorporation,
                };
            }
            return null;
        }

        function renderOptions(data, context) {
            if (!optionsContainer) {
                return;
            }
            optionsContainer.innerHTML = "";
            if (warningEl) {
                warningEl.classList.add("d-none");
            }

            var sectionsRendered = 0;

            function appendSection(items, scopeValue, kind, headingText) {
                if (!Array.isArray(items) || !items.length) {
                    return;
                }
                sectionsRendered += 1;

                var section = document.createElement("div");
                section.className = sectionsRendered > 1 ? "mb-3" : "";

                if (headingText) {
                    var heading = document.createElement("p");
                    heading.className = "text-uppercase small text-muted fw-semibold mb-2";
                    heading.textContent = headingText;
                    section.appendChild(heading);
                }

                items.forEach(function (item, index) {
                    var optionLabel = item && item.name ? item.name : (kind === "character" ? labelCharacter : labelCorporation);
                    var option = document.createElement("label");
                    option.className = "bp-scope-option d-flex align-items-start gap-3 border rounded-3 p-3 mb-2";
                    option.setAttribute("data-scope-option", "true");

                    var input = document.createElement("input");
                    input.type = "radio";
                    input.name = "scopeSelection";
                    input.value = scopeValue;
                    input.className = "form-check-input mt-1";
                    input.setAttribute("data-option-kind", kind);
                    input.setAttribute("data-option-kind-label", kind === "character" ? labelCharacter : labelCorporation);
                    input.setAttribute("data-option-label", optionLabel);

                    option.appendChild(input);

                    var content = document.createElement("div");
                    content.className = "flex-grow-1";

                    var titleRow = document.createElement("div");
                    titleRow.className = "d-flex flex-wrap align-items-center gap-2";

                    var titleText = document.createElement("span");
                    titleText.className = "fw-semibold text-body";
                    titleText.textContent = optionLabel;
                    titleRow.appendChild(titleText);

                    if (kind === "character" && item && item.is_self) {
                        var youBadge = document.createElement("span");
                        youBadge.className = "badge bg-primary-subtle text-primary";
                        youBadge.textContent = badgeYou;
                        titleRow.appendChild(youBadge);
                    }

                    if (kind === "corporation" && item && item.includes_self) {
                        var accessBadge = document.createElement("span");
                        accessBadge.className = "badge bg-info-subtle text-info";
                        accessBadge.textContent = badgeAccess;
                        titleRow.appendChild(accessBadge);
                    }

                    content.appendChild(titleRow);

                    var subtitleParts = [];
                    if (kind === "character" && item && item.corporation) {
                        subtitleParts.push(item.corporation);
                    }
                    if (kind === "corporation" && item && item.member_count) {
                        subtitleParts.push("x" + item.member_count);
                    }
                    if (subtitleParts.length) {
                        var subtitle = document.createElement("div");
                        subtitle.className = "text-muted small";
                        subtitle.textContent = subtitleParts.join(" - ");
                        content.appendChild(subtitle);
                    }

                    option.appendChild(content);

                    input.addEventListener("change", function () {
                        if (confirmBtn) {
                            confirmBtn.disabled = false;
                        }
                        if (warningEl) {
                            warningEl.classList.add("d-none");
                        }
                        highlightSelected(optionsContainer);
                    });

                    var previousSelection = scopeSelections[context.requestId];
                    var shouldPreselect = false;
                    if (previousSelection && previousSelection.scope === scopeValue) {
                        if (!previousSelection.label || previousSelection.label === optionLabel) {
                            shouldPreselect = true;
                        }
                    } else if (!previousSelection && context.defaultScope && context.defaultScope === scopeValue) {
                        shouldPreselect = true;
                    }

                    if (shouldPreselect) {
                        input.checked = true;
                    }

                    section.appendChild(option);
                });

                optionsContainer.appendChild(section);
            }

            appendSection(data.characters, "personal", "character", headingCharacters);
            appendSection(data.corporations, "corporation", "corporation", headingCorporations);

            highlightSelected(optionsContainer);

            var selectedInput = optionsContainer.querySelector('input[name="scopeSelection"]:checked');
            if (confirmBtn) {
                confirmBtn.disabled = !selectedInput;
            }
            if (!sectionsRendered && warningEl) {
                warningEl.textContent = warningMessage;
                warningEl.classList.remove("d-none");
            }
        }

        triggers.forEach(function (trigger) {
            trigger.addEventListener("click", function (event) {
                var requiresScope = trigger.getAttribute("data-scope-required");
                if (!requiresScope) {
                    return;
                }
                var requestId = trigger.getAttribute("data-request-id");
                if (!requestId) {
                    return;
                }
                var scriptId = trigger.getAttribute("data-scope-script-id") || ("bp-scope-options-" + requestId);
                var data = parseScopeData(scriptId);
                if (!data) {
                    return;
                }
                var personalAvailable = Array.isArray(data.characters) && data.characters.length;
                var corpAvailable = Array.isArray(data.corporations) && data.corporations.length;
                if (!personalAvailable || !corpAvailable) {
                    return;
                }
                event.preventDefault();

                var form = trigger.closest("form");
                var scopeInput = form ? form.querySelector("[data-scope-input]") : null;
                var defaultScope = scopeInput ? scopeInput.getAttribute("data-scope-default") || scopeInput.value || "" : "";

                currentContext = {
                    requestId: String(requestId),
                    form: form,
                    trigger: trigger,
                    data: data,
                    defaultScope: defaultScope,
                    action: (trigger.getAttribute("data-scope-action") || "").toLowerCase(),
                    openCollapseId: trigger.getAttribute("data-scope-open-collapse") || "",
                };

                if (useModal) {
                    resetModalState();
                    if (titleEl) {
                        var defaultTitle = titleEl.getAttribute("data-default-title") || "";
                        var actionLabel = trigger.getAttribute("data-scope-action-label") || "";
                        titleEl.textContent = actionLabel ? defaultTitle + " - " + actionLabel : defaultTitle;
                    }
                    if (helperEl) {
                        var defaultHelper = helperEl.getAttribute("data-default-helper") || "";
                        var helperExtras = [];
                        var actionName = trigger.getAttribute("data-scope-action-label");
                        if (actionName) {
                            helperExtras.push(actionName);
                        }
                        if (data.typeName) {
                            helperExtras.push(data.typeName);
                        }
                        helperEl.textContent = helperExtras.length ? defaultHelper + " (" + helperExtras.join(" - ") + ")" : defaultHelper;
                    }
                    renderOptions(data, currentContext);
                    if (modalController) {
                        modalController.show();
                    }
                    return;
                }

                var selection = promptSelection(data);
                if (selection) {
                    submitSelection(currentContext, selection);
                }
            });
        });
    }

    function initConditionalToggles() {
        var buttons = document.querySelectorAll("[data-conditional-toggle]");
        if (!buttons.length) {
            return;
        }
        Array.prototype.forEach.call(buttons, function (button) {
            button.addEventListener("click", function (event) {
                event.preventDefault();
                var targetId = button.getAttribute("data-conditional-target");
                if (!targetId) {
                    return;
                }
                revealCollapseSection(targetId);
                button.setAttribute("aria-expanded", "true");
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initCopyButtons();
        initScopeSelector();
        initConditionalToggles();
    });
})();
