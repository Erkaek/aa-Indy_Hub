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

    function initFulfillChatHeightSync() {
        var desktopQuery = null;
        if (typeof window.matchMedia === "function") {
            desktopQuery = window.matchMedia("(min-width: 1200px)");
        }

        var shells = Array.prototype.slice.call(
            document.querySelectorAll(".fulfill-reference-shell")
        );
        if (!shells.length) {
            return;
        }

        function resetChatHeight(chat) {
            if (!chat) {
                return;
            }
            chat.style.removeProperty("height");
            chat.style.removeProperty("min-height");
            chat.style.removeProperty("max-height");
        }

        function syncShell(shell) {
            if (!shell) {
                return;
            }

            var chat = shell.querySelector(".fulfill-reference-chat");
            if (!chat) {
                return;
            }

            if (desktopQuery && !desktopQuery.matches) {
                resetChatHeight(chat);
                return;
            }

            var availability = shell.querySelector(
                ".fulfill-reference-sidecard--availability"
            );
            if (!availability) {
                resetChatHeight(chat);
                return;
            }

            var chatRect = chat.getBoundingClientRect();
            var availabilityRect = availability.getBoundingClientRect();
            var targetHeight = Math.round(availabilityRect.bottom - chatRect.top);

            if (!isFinite(targetHeight) || targetHeight <= 0) {
                resetChatHeight(chat);
                return;
            }

            chat.style.setProperty("height", targetHeight + "px");
            chat.style.setProperty("min-height", targetHeight + "px");
            chat.style.setProperty("max-height", targetHeight + "px");
        }

        var rafId = 0;
        function scheduleSync() {
            if (rafId) {
                window.cancelAnimationFrame(rafId);
            }
            rafId = window.requestAnimationFrame(function () {
                rafId = 0;
                shells.forEach(syncShell);
            });
        }

        scheduleSync();
        window.addEventListener("resize", scheduleSync);
        window.addEventListener("load", scheduleSync);

        if (desktopQuery) {
            if (typeof desktopQuery.addEventListener === "function") {
                desktopQuery.addEventListener("change", scheduleSync);
            } else if (typeof desktopQuery.addListener === "function") {
                desktopQuery.addListener(scheduleSync);
            }
        }

        if (typeof window.ResizeObserver === "function") {
            var observer = new window.ResizeObserver(scheduleSync);
            shells.forEach(function (shell) {
                observer.observe(shell);

                var rail = shell.querySelector(".fulfill-reference-rail");
                if (rail) {
                    observer.observe(rail);
                }

                var actions = shell.querySelector(
                    ".fulfill-reference-sidecard--actions"
                );
                if (actions) {
                    observer.observe(actions);
                }

                var availability = shell.querySelector(
                    ".fulfill-reference-sidecard--availability"
                );
                if (availability) {
                    observer.observe(availability);
                }
            });
        }

        window.setTimeout(scheduleSync, 120);
        window.setTimeout(scheduleSync, 360);
    }

    function formatNumber(value, fractionDigits) {
        var number = Number(value || 0);
        if (!isFinite(number)) {
            number = 0;
        }
        return number.toLocaleString(undefined, {
            minimumFractionDigits: fractionDigits,
            maximumFractionDigits: fractionDigits,
        });
    }

    function formatISK(value) {
        return formatNumber(value, 0) + " ISK";
    }

    function formatPercent(value) {
        return formatNumber(value, 2) + "%";
    }

    function formatDurationCompact(totalSeconds) {
        var seconds = Math.max(0, parseInt(totalSeconds || 0, 10) || 0);
        if (!seconds) {
            return "-";
        }

        var days = Math.floor(seconds / 86400);
        var hours = Math.floor((seconds % 86400) / 3600);
        var minutes = Math.floor((seconds % 3600) / 60);
        var parts = [];

        if (days) {
            parts.push(days + "d");
        }
        if (hours) {
            parts.push(hours + "h");
        }
        if (minutes) {
            parts.push(minutes + "m");
        }
        if (!parts.length) {
            parts.push(seconds + "s");
        }

        return parts.join(" ");
    }

    function computeEffectiveCycleSeconds(
        baseTimeSeconds,
        characterTimeBonusPercent,
        structureTimeBonusPercent
    ) {
        var numericBaseTime = Math.max(0, parseInt(baseTimeSeconds || 0, 10) || 0);
        if (!numericBaseTime) {
            return 0;
        }

        var characterMultiplier = Math.max(
            0,
            1 - (Number(characterTimeBonusPercent || 0) / 100)
        );
        var structureMultiplier = Math.max(
            0,
            1 - (Number(structureTimeBonusPercent || 0) / 100)
        );

        return Math.max(
            1,
            Math.ceil(numericBaseTime * characterMultiplier * structureMultiplier)
        );
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

    function initFulfillWorkspace() {
        var roots = Array.prototype.slice.call(
            document.querySelectorAll("[data-fulfill-workspace]")
        );
        if (!roots.length) {
            return;
        }
        var autoOpenRoot = document.querySelector("[data-auto-open-chat]");

        roots.forEach(function (root) {
            var items = Array.prototype.slice.call(
                root.querySelectorAll("[data-fulfill-request-item]")
            );
            var panels = Array.prototype.slice.call(
                root.querySelectorAll("[data-fulfill-panel]")
            );
            var searchInput = root.querySelector("[data-fulfill-search]");
            var searchEmpty = root.querySelector("[data-fulfill-search-empty]");

            if (!items.length || !panels.length) {
                return;
            }

            function findPanel(requestId) {
                return panels.find(function (panel) {
                    return panel.getAttribute("data-fulfill-panel") === String(requestId);
                }) || null;
            }

            function visibleItems() {
                return items.filter(function (item) {
                    return !item.hidden;
                });
            }

            function closeInactiveChats(activeRequestId) {
                var shells = root.querySelectorAll("[data-chat-inline]");
                shells.forEach(function (shell) {
                    if (shell.getAttribute("data-request-id") === String(activeRequestId)) {
                        return;
                    }
                    shell.dispatchEvent(new CustomEvent("indyhub:chat-close"));
                });
            }

            function activateRequest(requestId, options) {
                var resolvedId = String(requestId);
                var selectedPanel = findPanel(resolvedId);
                if (!selectedPanel) {
                    return;
                }

                items.forEach(function (item) {
                    var isActive = item.getAttribute("data-request-id") === resolvedId;
                    item.classList.toggle("is-active", isActive);
                    item.setAttribute("aria-pressed", isActive ? "true" : "false");
                });

                panels.forEach(function (panel) {
                    var isActive = panel.getAttribute("data-fulfill-panel") === resolvedId;
                    panel.classList.toggle("is-active", isActive);
                    panel.hidden = !isActive;
                });

                closeInactiveChats(resolvedId);

                var shouldAutoload = !options || options.autoloadChat !== false;
                if (!shouldAutoload) {
                    return;
                }

                var trigger = selectedPanel.querySelector("[data-chat-autoload-trigger]");
                if (!trigger) {
                    return;
                }

                var shellId = trigger.getAttribute("data-chat-target");
                var shell = shellId ? document.getElementById(shellId) : null;
                if (shell && shell.dataset.chatLoaded === "1") {
                    return;
                }
                trigger.click();
            }

            function applySearchFilter() {
                var term = searchInput
                    ? String(searchInput.value || "").trim().toLowerCase()
                    : "";
                var matchCount = 0;

                items.forEach(function (item) {
                    var haystack = String(item.getAttribute("data-search") || "").toLowerCase();
                    var matches = !term || haystack.indexOf(term) !== -1;
                    item.hidden = !matches;
                    if (matches) {
                        matchCount += 1;
                    }
                });

                if (searchEmpty) {
                    searchEmpty.classList.toggle("d-none", matchCount !== 0);
                }

                var activeItem = items.find(function (item) {
                    return item.classList.contains("is-active") && !item.hidden;
                });

                if (!activeItem) {
                    var firstVisible = visibleItems()[0];
                    if (firstVisible) {
                        activateRequest(firstVisible.getAttribute("data-request-id"), {
                            autoloadChat: true,
                        });
                    }
                }
            }

            items.forEach(function (item) {
                item.addEventListener("click", function () {
                    activateRequest(item.getAttribute("data-request-id"), {
                        autoloadChat: true,
                    });
                });
            });

            if (searchInput) {
                searchInput.addEventListener("input", applySearchFilter);
            }

            var autoChatId = autoOpenRoot
                ? autoOpenRoot.getAttribute("data-auto-open-chat")
                : "";
            if (autoChatId) {
                var autoItem = items.find(function (item) {
                    return item.getAttribute("data-chat-id") === String(autoChatId);
                });
                if (autoItem) {
                    activateRequest(autoItem.getAttribute("data-request-id"), {
                        autoloadChat: true,
                    });
                    applySearchFilter();
                    return;
                }

                var autoPanel = panels.find(function (panel) {
                    return Boolean(
                        panel.querySelector(
                            '[data-chat-id="' + String(autoChatId) + '"]'
                        )
                    );
                });
                if (autoPanel) {
                    activateRequest(autoPanel.getAttribute("data-fulfill-panel"), {
                        autoloadChat: false,
                    });
                    applySearchFilter();
                    return;
                }
            }

            var firstVisibleItem = visibleItems()[0];
            if (firstVisibleItem) {
                activateRequest(firstVisibleItem.getAttribute("data-request-id"), {
                    autoloadChat: true,
                });
            }

            applySearchFilter();
        });
    }

    function initCopyStructureSelectors() {
        var panels = document.querySelectorAll("[data-fulfill-panel]");
        if (!panels.length) {
            return;
        }

        function updateEstimate(panel) {
            if (!panel) {
                return;
            }
            var select = panel.querySelector("[data-copy-structure-select]");
            var option = select ? select.options[select.selectedIndex] : null;
            var producerSelect = panel.querySelector("[data-copy-producer-select]");
            var producerOption = producerSelect
                ? producerSelect.options[producerSelect.selectedIndex]
                : null;

            var totalEl = panel.querySelector("[data-copy-estimate-total]");
            var structureEl = panel.querySelector("[data-copy-estimate-structure]");
            var metaEl = panel.querySelector("[data-copy-estimate-meta]");
            var durationTotalEl = panel.querySelector("[data-copy-duration-total]");
            var durationMetaEl = panel.querySelector("[data-copy-duration-meta]");

            if (totalEl && option) {
                var totalCost = option.getAttribute("data-total-installation-cost") || 0;
                if (
                    producerOption
                    && producerOption.getAttribute("data-is-alpha-clone") === "1"
                ) {
                    var alphaTotal = option.getAttribute("data-total-installation-cost-alpha");
                    if (alphaTotal) {
                        totalCost = alphaTotal;
                    }
                }
                totalEl.textContent = formatISK(totalCost);
            }

            if (structureEl && option) {
                var structureName = option.getAttribute("data-structure-name") || "";
                var solarSystemName = option.getAttribute("data-solar-system-name") || "";
                structureEl.textContent = solarSystemName
                    ? structureName + " · " + solarSystemName
                    : structureName;
            }

            if (metaEl && option) {
                var costIndex = option.getAttribute("data-system-cost-index-percent") || 0;
                var tax = option.getAttribute("data-facility-tax-percent") || 0;
                var surcharge = option.getAttribute("data-scc-surcharge-percent") || 0;
                metaEl.textContent = [
                    "SCI " + formatPercent(costIndex),
                    __("Tax") + " " + formatPercent(tax),
                    "SCC " + formatPercent(surcharge),
                ].join(" · ");
            }

            if (durationTotalEl) {
                var baseTimeSeconds = panel.getAttribute("data-copy-base-time-seconds") || 0;
                var runsRequested = Math.max(
                    1,
                    parseInt(panel.getAttribute("data-copy-runs-requested") || 1, 10) || 1
                );
                var copiesRequested = Math.max(
                    1,
                    parseInt(panel.getAttribute("data-copy-copies-requested") || 1, 10) || 1
                );
                var structureTimeBonus = option
                    ? option.getAttribute("data-time-bonus-percent") || 0
                    : 0;
                var characterTimeBonus = producerOption
                    ? producerOption.getAttribute("data-character-time-bonus-percent") || 0
                    : 0;
                var effectiveCycleSeconds = computeEffectiveCycleSeconds(
                    baseTimeSeconds,
                    characterTimeBonus,
                    structureTimeBonus
                );
                var perCopyDurationSeconds = effectiveCycleSeconds * runsRequested;
                var totalDurationSeconds = perCopyDurationSeconds * copiesRequested;

                durationTotalEl.textContent = formatDurationCompact(totalDurationSeconds);
            }

            if (durationMetaEl) {
                var structureBonus = Number(
                    option ? option.getAttribute("data-time-bonus-percent") || 0 : 0
                );
                var characterBonus = Number(
                    producerOption
                        ? producerOption.getAttribute("data-character-time-bonus-percent") || 0
                        : 0
                );
                var durationParts = [
                    __("Per copy") + " " + formatDurationCompact(perCopyDurationSeconds),
                ];

                if (structureBonus > 0) {
                    durationParts.push(
                        __("Structure bonus") + " -" + formatPercent(structureBonus)
                    );
                }

                if (characterBonus > 0) {
                    durationParts.push(
                        __("Character bonus") + " -" + formatPercent(characterBonus)
                    );
                } else {
                    durationParts.push(__("Character skills not included."));
                }
                durationMetaEl.textContent = durationParts.join(" · ");
            }
        }

        panels.forEach(function (panel) {
            var structureSelect = panel.querySelector("[data-copy-structure-select]");
            var producerSelect = panel.querySelector("[data-copy-producer-select]");

            updateEstimate(panel);

            if (structureSelect) {
                structureSelect.addEventListener("change", function () {
                    updateEstimate(panel);
                });
            }

            if (producerSelect) {
                producerSelect.addEventListener("change", function () {
                    updateEstimate(panel);
                });
            }
        });
    }

    function initCopyCostBreakdownModal() {
        var modalEl = document.getElementById("bpCopyCostBreakdownModal");
        if (!modalEl) {
            return;
        }
        var triggers = document.querySelectorAll("[data-copy-cost-breakdown-trigger]");
        if (!triggers.length) {
            return;
        }

        // The fulfill page wraps the modal inside `.fulfill-page` which has
        // `isolation: isolate`, creating a new stacking context that traps the
        // modal underneath the body-level backdrop. Move the modal element to
        // `<body>` so Bootstrap can layer it above the backdrop properly.
        if (modalEl.parentNode !== document.body) {
            document.body.appendChild(modalEl);
        }

        var bootstrapModalCtor = null;
        if (typeof window !== "undefined") {
            if (window.bootstrap && window.bootstrap.Modal) {
                bootstrapModalCtor = window.bootstrap.Modal;
            } else if (window.bootstrap5 && window.bootstrap5.Modal) {
                bootstrapModalCtor = window.bootstrap5.Modal;
            }
        }
        if (!bootstrapModalCtor) {
            return;
        }
        var modal = (typeof bootstrapModalCtor.getOrCreateInstance === "function")
            ? bootstrapModalCtor.getOrCreateInstance(modalEl)
            : new bootstrapModalCtor(modalEl);

        var labels = {
            eiv: modalEl.getAttribute("data-label-eiv") || "Estimated items value (EIV)",
            jcb: modalEl.getAttribute("data-label-jcb") || "Job cost base (JCB)",
            sci: modalEl.getAttribute("data-label-sci") || "System cost index",
            grossSection: modalEl.getAttribute("data-label-gross-section") || "JOB GROSS COST",
            gross: modalEl.getAttribute("data-label-gross") || "Total job gross cost",
            taxesSection: modalEl.getAttribute("data-label-taxes-section") || "TAXES",
            structureBonus: modalEl.getAttribute("data-label-structure-bonus") || "Structure role bonus",
            rigBonus: modalEl.getAttribute("data-label-rig-bonus") || "Rig bonus",
            adjusted: modalEl.getAttribute("data-label-adjusted") || "Adjusted job cost",
            facilityTax: modalEl.getAttribute("data-label-facility-tax") || "Facility tax",
            scc: modalEl.getAttribute("data-label-scc") || "SCC surcharge",
            alphaTax: modalEl.getAttribute("data-label-alpha-tax") || "Alpha clone tax",
            totalTaxes: modalEl.getAttribute("data-label-total-taxes") || "Total taxes",
            totalJob: modalEl.getAttribute("data-label-total-job") || "Total job cost (per copy)",
            grandTotal: modalEl.getAttribute("data-label-grand-total") || "Total install cost",
            copySingular: modalEl.getAttribute("data-label-copy-singular") || "copy",
            copyPlural: modalEl.getAttribute("data-label-copy-plural") || "copies",
            runs: modalEl.getAttribute("data-label-runs") || "Runs per copy",
            copies: modalEl.getAttribute("data-label-copies") || "Copies requested",
            isk: modalEl.getAttribute("data-label-isk") || "ISK"
        };

        var structureEl = modalEl.querySelector("[data-cost-bd-structure]");
        var contextEl = modalEl.querySelector("[data-cost-bd-context]");
        var rowsEl = modalEl.querySelector("[data-cost-bd-rows]");

        function toNumber(value) {
            if (value === null || value === undefined || value === "") {
                return 0;
            }
            var n = Number(value);
            return isFinite(n) ? n : 0;
        }

        function formatIsk(value) {
            var n = Math.round(toNumber(value));
            try {
                return n.toLocaleString(undefined, {
                    minimumFractionDigits: 0,
                    maximumFractionDigits: 0
                }) + " " + labels.isk;
            } catch (err) {
                return String(n) + " " + labels.isk;
            }
        }

        function formatPercent(value, digits) {
            var d = (typeof digits === "number") ? digits : 2;
            var n = toNumber(value);
            try {
                return n.toLocaleString(undefined, {
                    minimumFractionDigits: d,
                    maximumFractionDigits: d
                }) + " %";
            } catch (err) {
                return n.toFixed(d) + " %";
            }
        }

        function formatInteger(value) {
            var n = Math.trunc(toNumber(value));
            try {
                return n.toLocaleString();
            } catch (err) {
                return String(n);
            }
        }

        function escapeHtml(value) {
            return String(value === null || value === undefined ? "" : value)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        function row(label, value, modifier) {
            var cls = modifier ? ' class="' + escapeHtml(modifier) + '"' : "";
            return "<tr" + cls + "><td>" + escapeHtml(label) + "</td><td>" + escapeHtml(value) + "</td></tr>";
        }

        function headerRow(label) {
            return '<tr class="fwp-cost-breakdown-table__header"><td colspan="2">'
                + escapeHtml(label) + "</td></tr>";
        }

        function grandRow(label, value) {
            return '<tr class="fwp-cost-breakdown-table__grand"><td>'
                + escapeHtml(label) + "</td><td>" + escapeHtml(value) + "</td></tr>";
        }

        function readPayload(payloadId) {
            var node = document.getElementById(payloadId);
            if (!node) {
                return null;
            }
            try {
                return JSON.parse(node.textContent || "null");
            } catch (err) {
                return null;
            }
        }

        function render(payload) {
            if (!payload) {
                if (structureEl) structureEl.textContent = "";
                if (contextEl) contextEl.textContent = "";
                if (rowsEl) rowsEl.innerHTML = "";
                return;
            }

            var structureLine = payload.structure_name || "";
            if (payload.solar_system_name) {
                structureLine += structureLine ? " · " + payload.solar_system_name : payload.solar_system_name;
            }
            if (structureEl) {
                structureEl.textContent = structureLine;
            }
            if (contextEl) {
                var copies = formatInteger(payload.copies_requested);
                var runs = formatInteger(payload.runs_requested);
                contextEl.textContent = labels.copies + ": " + copies + " · " + labels.runs + ": " + runs;
            }

            if (!rowsEl) {
                return;
            }

            var html = "";

            // Inputs
            html += row(labels.eiv, formatIsk(payload.estimated_item_value));
            html += row(
                labels.jcb + " (" + formatPercent(payload.jcb_percent, 2) + ")",
                formatIsk(payload.job_cost_base)
            );

            // ── Job gross cost section ────────────────────────────
            html += headerRow(labels.grossSection || "JOB GROSS COST");
            html += row(
                labels.sci + " (" + formatPercent(payload.system_cost_index_percent, 2) + ")",
                formatIsk(payload.base_job_cost)
            );
            if (toNumber(payload.structure_role_bonus_percent) > 0) {
                html += row(
                    labels.structureBonus,
                    "−" + formatPercent(payload.structure_role_bonus_percent, 2),
                    "fwp-cost-breakdown-table__success"
                );
            }
            if (toNumber(payload.rig_bonus_percent) > 0) {
                html += row(
                    labels.rigBonus,
                    "−" + formatPercent(payload.rig_bonus_percent, 2),
                    "fwp-cost-breakdown-table__success"
                );
            }
            html += row(
                labels.gross,
                formatIsk(payload.adjusted_job_cost),
                "fwp-cost-breakdown-table__bold"
            );

            // ── Taxes section ─────────────────────────────────────
            html += headerRow(labels.taxesSection || "TAXES");
            html += row(
                labels.facilityTax + " (" + formatPercent(payload.facility_tax_percent, 2) + ")",
                formatIsk(payload.facility_tax),
                "fwp-cost-breakdown-table__danger"
            );
            if (payload.is_alpha_clone && toNumber(payload.alpha_clone_tax_percent) > 0) {
                html += row(
                    labels.alphaTax + " (" + formatPercent(payload.alpha_clone_tax_percent, 2) + ")",
                    formatIsk(payload.alpha_clone_tax),
                    "fwp-cost-breakdown-table__danger"
                );
            }
            html += row(
                labels.scc + " (" + formatPercent(payload.scc_surcharge_percent, 2) + ")",
                formatIsk(payload.scc_surcharge),
                "fwp-cost-breakdown-table__danger"
            );
            html += row(
                labels.totalTaxes,
                formatIsk(payload.total_taxes),
                "fwp-cost-breakdown-table__bold"
            );

            // ── Per-copy info (secondary) + Grand total ───────────
            if (toNumber(payload.copies_requested) > 1) {
                html += row(
                    labels.totalJob,
                    formatIsk(payload.per_copy_installation_cost),
                    "fwp-cost-breakdown-table__muted"
                );
            }
            html += grandRow(
                labels.grandTotal,
                formatIsk(payload.total_installation_cost)
            );
            rowsEl.innerHTML = html;
        }

        Array.prototype.forEach.call(triggers, function (trigger) {
            trigger.addEventListener("click", function (event) {
                event.preventDefault();
                var payloadId = trigger.getAttribute("data-copy-cost-breakdown-id");
                var prefix = trigger.getAttribute("data-copy-cost-breakdown-prefix");
                var requestId = trigger.getAttribute("data-request-id");
                var producerIsAlpha = false;
                if (requestId) {
                    var producerSelect = document.querySelector(
                        '[data-copy-producer-select][data-request-id="' + requestId + '"]'
                    );
                    if (producerSelect) {
                        var producerOption = producerSelect.options[producerSelect.selectedIndex];
                        if (producerOption) {
                            producerIsAlpha = producerOption.getAttribute("data-is-alpha-clone") === "1";
                        }
                    }
                }
                if (prefix && requestId) {
                    var select = document.querySelector(
                        '[data-copy-structure-select][data-request-id="' + requestId + '"]'
                    );
                    if (select && select.value) {
                        var perStructureId = prefix + select.value;
                        if (document.getElementById(perStructureId)) {
                            payloadId = perStructureId;
                        }
                    }
                }
                if (!payloadId) {
                    return;
                }
                var payload = readPayload(payloadId);
                if (payload) {
                    var serverAlpha = !!payload.is_alpha_clone;
                    if (producerIsAlpha !== serverAlpha) {
                        var alphaTaxAmount = toNumber(payload.alpha_clone_tax);
                        var copiesCount = Math.max(
                            1,
                            toNumber(payload.copies_requested) || 1
                        );
                        var alphaTaxPerCopy = alphaTaxAmount / copiesCount;
                        var baseTotal = toNumber(payload.total_installation_cost);
                        var baseTaxes = toNumber(payload.total_taxes);
                        var basePerCopy = toNumber(payload.per_copy_installation_cost);
                        // Reverse server‐side alpha contribution if needed.
                        if (serverAlpha) {
                            baseTotal -= alphaTaxAmount;
                            baseTaxes -= alphaTaxAmount;
                            basePerCopy = Math.max(0, basePerCopy - alphaTaxPerCopy);
                        }
                        if (producerIsAlpha) {
                            payload.is_alpha_clone = true;
                            payload.total_installation_cost = baseTotal + alphaTaxAmount;
                            payload.total_taxes = baseTaxes + alphaTaxAmount;
                            payload.per_copy_installation_cost =
                                basePerCopy + alphaTaxPerCopy;
                        } else {
                            payload.is_alpha_clone = false;
                            payload.total_installation_cost = baseTotal;
                            payload.total_taxes = baseTaxes;
                            payload.per_copy_installation_cost = basePerCopy;
                        }
                    }
                }
                render(payload);
                modal.show();
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initFulfillChatHeightSync();
        initCopyButtons();
        initScopeSelector();
        initConditionalToggles();
        initFulfillWorkspace();
        initCopyStructureSelectors();
        initCopyCostBreakdownModal();
    });
})();
