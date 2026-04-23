(function () {
    var CHAT_POLL_INTERVAL_MS = 10000;

    var debugEnabled =
        typeof window !== "undefined" && window.INDY_HUB_DEBUG === true;

    function debugLog() {
        if (
            !debugEnabled ||
            typeof console === "undefined" ||
            typeof console.debug !== "function"
        ) {
            return;
        }
        console.debug.apply(console, arguments);
    }

    function __(message) {
        if (typeof window !== "undefined" && typeof window.gettext === "function") {
            return window.gettext(message);
        }
        return message;
    }

    function $(selector, root) {
        return (root || document).querySelector(selector);
    }

    function createEl(tag, className, text) {
        var el = document.createElement(tag);
        if (className) {
            el.className = className;
        }
        if (typeof text === "string") {
            el.textContent = text;
        }
        return el;
    }

    function scrollToBottom(container) {
        if (!container) {
            return;
        }
        container.scrollTop = container.scrollHeight;
    }

    function scrollMessagesToBottom(container) {
        if (!container) {
            return;
        }
        if (
            typeof window !== "undefined" &&
            typeof window.requestAnimationFrame === "function"
        ) {
            window.requestAnimationFrame(function () {
                window.requestAnimationFrame(function () {
                    scrollToBottom(container);
                });
            });
            return;
        }
        scrollToBottom(container);
    }

    function scrollMessagesToBottomNow(container) {
        if (!container) {
            return;
        }
        scrollToBottom(container);
        if (typeof window !== "undefined") {
            window.setTimeout(function () {
                scrollToBottom(container);
            }, 50);
        }
    }

    function withViewerRole(url, viewerRole) {
        if (!url || !viewerRole) {
            return url;
        }
        try {
            var origin =
                typeof window !== "undefined" &&
                window.location &&
                window.location.origin
                    ? window.location.origin
                    : undefined;
            var resolved = new URL(url, origin);
            resolved.searchParams.set("viewer_role", viewerRole);
            if (
                typeof window !== "undefined" &&
                window.location &&
                resolved.origin === window.location.origin
            ) {
                return resolved.pathname + resolved.search + resolved.hash;
            }
            return resolved.toString();
        } catch (err) {
            var separator = url.indexOf("?") === -1 ? "?" : "&";
            return (
                url + separator + "viewer_role=" + encodeURIComponent(viewerRole)
            );
        }
    }

    function labelFor(role, viewerRole, labels) {
        if (role === viewerRole) {
            return labels.you || "You";
        }
        return labels[role] || role;
    }

    function loadSeenChatIds() {
        if (typeof window === "undefined" || !window.localStorage) {
            return [];
        }
        try {
            var raw = window.localStorage.getItem("indyhub_seen_chats");
            if (!raw) {
                return [];
            }
            var parsed = JSON.parse(raw);
            if (!Array.isArray(parsed)) {
                return [];
            }
            return parsed.map(function (value) {
                return String(value);
            });
        } catch (err) {
            return [];
        }
    }

    function storeSeenChatId(chatId) {
        if (!chatId || typeof window === "undefined" || !window.localStorage) {
            return;
        }
        var stringId = String(chatId);
        var seen = loadSeenChatIds();
        if (seen.indexOf(stringId) !== -1) {
            return;
        }
        seen.unshift(stringId);
        if (seen.length > 100) {
            seen = seen.slice(0, 100);
        }
        try {
            window.localStorage.setItem("indyhub_seen_chats", JSON.stringify(seen));
        } catch (err) {
            return;
        }
    }

    function getBootstrapModalCtor() {
        if (typeof window === "undefined") {
            return null;
        }
        if (window.bootstrap && window.bootstrap.Modal) {
            return window.bootstrap.Modal;
        }
        if (window.bootstrap5 && window.bootstrap5.Modal) {
            return window.bootstrap5.Modal;
        }
        return null;
    }

    function syncRequestActionPanels(detail) {
        if (!detail || !detail.requestId) {
            return;
        }

        var requestId = String(detail.requestId);
        var panels = Array.prototype.slice.call(
            document.querySelectorAll("[data-copy-request-actions]")
        ).filter(function (panel) {
            return panel.getAttribute("data-request-id") === requestId;
        });

        if (!panels.length) {
            return;
        }

        var decision = detail.decision || null;
        var hasCurrentAmount = Boolean(
            decision && decision.current_amount_display
        );
        var negotiationSettled = Boolean(
            detail.chat &&
                (!decision ||
                    (decision.accepted_by_buyer && decision.accepted_by_seller))
        );
        var noteText = decision
            ? [decision.status_label, decision.hint_label]
                .filter(Boolean)
                .join(" ")
            : "";

        panels.forEach(function (panel) {
            var offerActionsEl = panel.querySelector("[data-request-offer-actions]");
            var negotiationActionsEl = panel.querySelector(
                "[data-request-negotiation-actions]"
            );
            var noteEl = panel.querySelector("[data-request-negotiation-note]");
            var noteTextEl = panel.querySelector(
                "[data-request-negotiation-note-text]"
            );
            var acceptFormEl = panel.querySelector("[data-request-accept-form]");
            var acceptBtnEl = acceptFormEl
                ? acceptFormEl.querySelector("button[type='submit']")
                : null;
            var declineFormEl = panel.querySelector("[data-request-decline-form]");
            var declineBtnEl = declineFormEl
                ? declineFormEl.querySelector("button[type='submit']")
                : null;

            if (offerActionsEl) {
                offerActionsEl.classList.toggle(
                    "d-none",
                    hasCurrentAmount || negotiationSettled
                );
            }

            if (negotiationActionsEl) {
                negotiationActionsEl.classList.toggle(
                    "d-none",
                    !hasCurrentAmount || negotiationSettled
                );
            }

            if (noteEl) {
                noteEl.classList.toggle("d-none", !noteText || negotiationSettled);
            }

            if (noteTextEl) {
                noteTextEl.textContent = noteText;
            }

            if (acceptFormEl) {
                acceptFormEl.classList.toggle(
                    "d-none",
                    !(
                        hasCurrentAmount &&
                        !negotiationSettled &&
                        decision &&
                        decision.viewer_can_accept
                    )
                );
            }

            if (acceptBtnEl) {
                acceptBtnEl.disabled = !(
                    hasCurrentAmount &&
                    !negotiationSettled &&
                    decision &&
                    decision.viewer_can_accept
                );
                if (decision && decision.accept_label) {
                    acceptBtnEl.innerHTML =
                        '<i class="fas fa-check"></i>' + decision.accept_label;
                }
            }

            if (declineFormEl) {
                declineFormEl.classList.toggle(
                    "d-none",
                    !(
                        hasCurrentAmount &&
                        !negotiationSettled &&
                        decision &&
                        decision.viewer_can_reject
                    )
                );
            }

            if (declineBtnEl) {
                declineBtnEl.disabled = !(
                    hasCurrentAmount &&
                    !negotiationSettled &&
                    decision &&
                    decision.viewer_can_reject
                );
                if (decision && decision.reject_label) {
                    declineBtnEl.innerHTML =
                        '<i class="fas fa-times"></i>' + decision.reject_label;
                }
            }
        });
    }

    document.addEventListener("indyhub:bp-chat-state", function (event) {
        syncRequestActionPanels(event.detail || null);
    });

    function createChatController(shellEl) {
        var inlineMode = shellEl.hasAttribute("data-chat-inline");
        var bootstrapModalCtor = getBootstrapModalCtor();
        var useBootstrap = !inlineMode && Boolean(bootstrapModalCtor);
        var modal = useBootstrap
            ? bootstrapModalCtor.getOrCreateInstance(shellEl)
            : null;
        var backdropEl = null;
        var previousBodyOverflow = "";

        var formEl = $("[data-chat-form]", shellEl);
        var messageContainer = $("[data-chat-messages]", shellEl);
        var statusEl = $("[data-chat-status]", shellEl);
        var summaryEl = $("[data-chat-summary]", shellEl);
        var inputEl = $("[data-chat-input]", shellEl);
        var actionsEl = $("[data-chat-actions]", shellEl);
        var actionStatusEl = actionsEl
            ? $("[data-chat-action-status]", actionsEl)
            : null;
        var actionHintEl = actionsEl
            ? $("[data-chat-action-hint]", actionsEl)
            : null;
        var actionButtonsEl = actionsEl
            ? $("[data-chat-action-buttons]", actionsEl)
            : null;
        var acceptBtnEl = actionsEl ? $("[data-chat-accept]", actionsEl) : null;
        var proposalFormEl = actionsEl
            ? $("[data-chat-proposal-form]", actionsEl)
            : null;
        var proposalInputEl = actionsEl
            ? $("[data-chat-proposal-input]", actionsEl)
            : null;
        var proposalSubmitBtn = actionsEl
            ? $("[data-chat-proposal-submit]", actionsEl)
            : null;
        var proposalCurrentEl = actionsEl
            ? $("[data-chat-proposal-current]", actionsEl)
            : null;
        var proposalAmountEl = actionsEl
            ? $("[data-chat-proposal-amount]", actionsEl)
            : null;

        if (!formEl || !messageContainer || !inputEl) {
            return null;
        }

        var state = {
            fetchUrl: null,
            sendUrl: null,
            viewerRole: "buyer",
            labels: {
                buyer: __("Buyer"),
                seller: __("Builder"),
                system: __("System"),
                you: __("You"),
            },
            typeName: "",
            typeId: null,
            polling: null,
            isOpen: false,
            decisionUrl: null,
            lastDecision: null,
            actionSubmitting: false,
            pendingInitialScroll: false,
            initialScrollDone: false,
        };

        var defaults = window.indyChatDefaults || {};
        if (defaults.viewerRole) {
            state.viewerRole = defaults.viewerRole;
        }
        if (defaults.labels) {
            state.labels = Object.assign({}, state.labels, defaults.labels);
        }

        function getCsrfToken() {
            var input = $("input[name='csrfmiddlewaretoken']", formEl);
            if (input && input.value) {
                return input.value;
            }
            if (typeof window !== "undefined" && window.csrfToken) {
                return window.csrfToken;
            }
            var match = document.cookie
                ? document.cookie.match(/csrftoken=([^;]+)/)
                : null;
            if (match && match[1]) {
                try {
                    return decodeURIComponent(match[1]);
                } catch (err) {
                    return match[1];
                }
            }
            return "";
        }

        function ensureBackdrop() {
            if (backdropEl || inlineMode) {
                return;
            }
            backdropEl = document.createElement("div");
            backdropEl.className = "modal-backdrop fade show";
            document.body.appendChild(backdropEl);
        }

        function removeBackdrop() {
            if (!backdropEl) {
                return;
            }
            if (backdropEl.parentNode) {
                backdropEl.parentNode.removeChild(backdropEl);
            }
            backdropEl = null;
        }

        function showShell() {
            if (inlineMode) {
                shellEl.classList.add("is-active");
                return;
            }
            if (useBootstrap) {
                modal.show();
                return;
            }
            if (shellEl.classList.contains("show")) {
                return;
            }
            ensureBackdrop();
            shellEl.style.display = "block";
            shellEl.classList.add("show");
            shellEl.removeAttribute("aria-hidden");
            document.body.classList.add("modal-open");
            previousBodyOverflow = document.body.style.overflow || "";
            document.body.style.overflow = "hidden";
        }

        function stopPolling() {
            if (state.polling) {
                window.clearInterval(state.polling);
                state.polling = null;
            }
        }

        function clearMessages() {
            while (messageContainer.firstChild) {
                messageContainer.removeChild(messageContainer.firstChild);
            }
        }

        function resetStatus() {
            if (!statusEl) {
                return;
            }
            statusEl.classList.add("d-none");
            statusEl.textContent = "";
            statusEl.classList.remove(
                "alert-danger",
                "alert-warning",
                "alert-info",
                "alert-success"
            );
        }

        function showStatus(message, tone) {
            if (!statusEl) {
                return;
            }
            if (!message) {
                resetStatus();
                return;
            }
            var toneClass = "alert-info";
            if (tone === "error") {
                toneClass = "alert-danger";
            } else if (tone === "warning") {
                toneClass = "alert-warning";
            } else if (tone === "success") {
                toneClass = "alert-success";
            }
            statusEl.classList.remove(
                "alert-danger",
                "alert-warning",
                "alert-info",
                "alert-success",
                "d-none"
            );
            statusEl.classList.add(toneClass);
            statusEl.textContent = message;
        }

        function toggleForm(enabled) {
            var disabled = !enabled;
            if (disabled) {
                formEl.setAttribute("aria-disabled", "true");
            } else {
                formEl.removeAttribute("aria-disabled");
            }
            inputEl.disabled = disabled;
            var submitBtn = $("button[type='submit']", formEl);
            if (submitBtn) {
                submitBtn.disabled = disabled;
            }
        }

        function updateSummary(payload) {
            if (!summaryEl) {
                return;
            }
            var typeName = payload.chat.type_name || state.typeName || __("Blueprint");
            var typeId = payload.chat.type_id || state.typeId || null;
            var viewerLabel =
                state.labels[payload.chat.viewer_role] || payload.chat.viewer_role;
            var otherLabel =
                state.labels[payload.chat.other_role] || payload.chat.other_role;

            summaryEl.innerHTML = "";
            var panel = createEl("div", "bp-chat-summary__panel");
            var headline = createEl("div", "bp-chat-summary__headline");
            var nameEl = createEl("span", "bp-chat-summary__type", typeName);
            headline.appendChild(nameEl);

            if (!payload.chat.is_open && payload.chat.closed_reason) {
                var reasonLabels = {
                    request_closed: __("Request closed"),
                    offer_accepted: __("Offer accepted"),
                    offer_rejected: __("Offer rejected"),
                    expired: __("Expired"),
                    manual: __("Closed"),
                    reopened: __("Reopened"),
                };
                var reasonKey = payload.chat.closed_reason;
                var closeLabel =
                    reasonLabels[reasonKey] || reasonKey.replace(/_/g, " ");
                var closedBadge = createEl("span", "bp-chat-summary__badge");
                closedBadge.textContent = closeLabel;
                headline.appendChild(closedBadge);
            }
            panel.appendChild(headline);

            var roles = createEl("div", "bp-chat-summary__roles");
            roles.appendChild(
                createEl(
                    "span",
                    "bp-chat-summary__role badge rounded-pill bg-primary-subtle text-primary fw-semibold",
                    viewerLabel
                )
            );
            roles.appendChild(createEl("span", "bp-chat-summary__divider", "↔"));
            roles.appendChild(
                createEl(
                    "span",
                    "bp-chat-summary__role badge rounded-pill bg-secondary-subtle text-secondary fw-semibold",
                    otherLabel
                )
            );
            panel.appendChild(roles);

            var detailParts = [];
            if (typeof payload.chat.material_efficiency === "number") {
                detailParts.push("ME " + payload.chat.material_efficiency);
            }
            if (typeof payload.chat.time_efficiency === "number") {
                detailParts.push("TE " + payload.chat.time_efficiency);
            }
            if (typeof payload.chat.runs_requested === "number") {
                detailParts.push(payload.chat.runs_requested + " " + __("runs"));
            }
            if (typeof payload.chat.copies_requested === "number") {
                detailParts.push(
                    payload.chat.copies_requested + " " + __("copies")
                );
            }

            if (detailParts.length) {
                panel.appendChild(
                    createEl(
                        "div",
                        "bp-chat-summary__meta text-muted small",
                        detailParts.join(" · ")
                    )
                );
            }

            if (typeId) {
                panel.appendChild(
                    createEl(
                        "div",
                        "bp-chat-summary__meta text-muted small",
                        "#" + typeId
                    )
                );
            }

            summaryEl.appendChild(panel);
        }

        function renderMessages(payload) {
            clearMessages();
            var viewerRole = payload.chat.viewer_role;
            var labels = Object.assign({}, state.labels);
            if (!labels[payload.chat.other_role]) {
                labels[payload.chat.other_role] = payload.chat.other_role;
            }

            (payload.messages || []).forEach(function (item) {
                var isProposal = item.kind === "proposal";
                var isSystem = item.role === "system" || isProposal;
                var roleLabel = isProposal
                    ? item.kind_label || __("Negotiation")
                    : labelFor(item.role, viewerRole, labels);
                var row = createEl("div", "bp-chat-row");
                var bubble = createEl("div", "bp-chat-message");
                if (isProposal) {
                    row.classList.add("bp-chat-row--system", "bp-chat-row--proposal");
                    bubble.classList.add("bp-chat-message--proposal");
                } else if (item.role === viewerRole) {
                    row.classList.add("bp-chat-row--self");
                    bubble.classList.add("bp-chat-message--self");
                } else if (isSystem) {
                    row.classList.add("bp-chat-row--system");
                    bubble.classList.add("bp-chat-message--system");
                } else {
                    row.classList.add("bp-chat-row--other");
                    bubble.classList.add("bp-chat-message--other");
                }

                var meta = createEl("div", "bp-chat-message__meta");
                meta.appendChild(
                    createEl(
                        "span",
                        "bp-chat-message__author",
                        roleLabel
                    )
                );
                meta.appendChild(
                    createEl("span", "bp-chat-message__separator", "•")
                );
                var timestamp = createEl(
                    "time",
                    "bp-chat-message__time",
                    item.created_display
                );
                if (item.created_at) {
                    timestamp.setAttribute("datetime", item.created_at);
                }
                meta.appendChild(timestamp);
                bubble.appendChild(meta);
                bubble.appendChild(
                    createEl("span", "bp-chat-message__content", item.content)
                );

                if (isSystem) {
                    row.appendChild(bubble);
                    messageContainer.appendChild(row);
                    return;
                }

                row.appendChild(bubble);

                messageContainer.appendChild(row);
            });

            if (state.pendingInitialScroll) {
                scrollMessagesToBottomNow(messageContainer);
                state.pendingInitialScroll = false;
                state.initialScrollDone = true;
            }
        }

        function updateActions(decision) {
            if (!actionsEl) {
                return;
            }
            state.lastDecision = decision || null;
            state.decisionUrl = decision && decision.url ? decision.url : null;

            if (!decision) {
                actionsEl.classList.add("d-none");
                delete actionsEl.dataset.chatState;
                delete actionsEl.dataset.chatHasCurrent;
                if (actionStatusEl) {
                    actionStatusEl.textContent = "";
                    actionStatusEl.classList.remove(
                        "text-danger",
                        "text-warning",
                        "text-success",
                        "text-primary",
                        "text-muted"
                    );
                }
                if (actionHintEl) {
                    actionHintEl.textContent = "";
                }
                if (actionButtonsEl) {
                    actionButtonsEl.classList.add("d-none");
                }
                if (acceptBtnEl) {
                    acceptBtnEl.disabled = true;
                }
                if (proposalFormEl) {
                    proposalFormEl.classList.add("d-none");
                }
                if (proposalInputEl) {
                    proposalInputEl.disabled = true;
                    proposalInputEl.value = "";
                }
                if (proposalSubmitBtn) {
                    proposalSubmitBtn.disabled = true;
                }
                if (proposalCurrentEl) {
                    proposalCurrentEl.classList.add("d-none");
                }
                if (proposalAmountEl) {
                    proposalAmountEl.textContent = "";
                }
                return;
            }

            actionsEl.dataset.chatState = decision.state || "";
            actionsEl.dataset.chatHasCurrent = decision.current_amount_display
                ? "true"
                : "false";

            var toneMap = {
                error: "text-danger",
                warning: "text-warning",
                success: "text-success",
                info: "text-primary",
            };

            if (actionStatusEl) {
                actionStatusEl.classList.remove(
                    "text-danger",
                    "text-warning",
                    "text-success",
                    "text-primary",
                    "text-muted"
                );
                if (decision.status_label) {
                    actionStatusEl.textContent = decision.status_label;
                    var toneClass =
                        decision.status_tone && toneMap[decision.status_tone]
                            ? toneMap[decision.status_tone]
                            : "text-muted";
                    actionStatusEl.classList.add(toneClass);
                } else {
                    actionStatusEl.textContent = "";
                }
            }

            if (actionHintEl) {
                actionHintEl.textContent = decision.hint_label || "";
            }

            var canPropose = Boolean(decision.viewer_can_propose);
            var canAccept = Boolean(decision.viewer_can_accept);

            if (proposalCurrentEl) {
                proposalCurrentEl.classList.toggle(
                    "d-none",
                    !decision.current_amount_display
                );
            }

            if (proposalAmountEl) {
                proposalAmountEl.textContent = decision.current_amount_display
                    ? decision.current_amount_display + " ISK"
                    : "";
            }

            if (proposalInputEl) {
                proposalInputEl.placeholder =
                    decision.proposal_placeholder || __("Enter amount in ISK");
                if (!proposalInputEl.value && decision.current_amount) {
                    proposalInputEl.value = decision.current_amount;
                }
                proposalInputEl.disabled = !canPropose || state.actionSubmitting;
            }

            if (proposalSubmitBtn) {
                if (decision.proposal_label) {
                    proposalSubmitBtn.innerHTML =
                        '<i class="fas fa-coins me-1"></i>' + decision.proposal_label;
                }
                proposalSubmitBtn.disabled = !canPropose || state.actionSubmitting;
            }

            if (proposalFormEl) {
                proposalFormEl.classList.toggle("d-none", !canPropose);
            }

            if (actionButtonsEl) {
                actionButtonsEl.classList.toggle("d-none", !canAccept);
            }

            if (acceptBtnEl) {
                if (decision.accept_label) {
                    acceptBtnEl.innerHTML =
                        '<i class="fas fa-check me-1"></i>' + decision.accept_label;
                }
                acceptBtnEl.disabled = !canAccept || state.actionSubmitting;
            }

            actionsEl.classList.toggle(
                "d-none",
                !(decision || Boolean(decision.status_label) || canPropose || canAccept)
            );
        }

        function setActionSubmitting(submitting) {
            state.actionSubmitting = submitting;
            if (!state.lastDecision) {
                return;
            }
            if (proposalInputEl && proposalFormEl && !proposalFormEl.classList.contains("d-none")) {
                proposalInputEl.disabled =
                    submitting || !state.lastDecision.viewer_can_propose;
            }
            if (proposalSubmitBtn && proposalFormEl && !proposalFormEl.classList.contains("d-none")) {
                proposalSubmitBtn.disabled =
                    submitting || !state.lastDecision.viewer_can_propose;
            }
            if (acceptBtnEl && actionButtonsEl && !actionButtonsEl.classList.contains("d-none")) {
                acceptBtnEl.disabled =
                    submitting || !state.lastDecision.viewer_can_accept;
            }
        }

        function applyChatState(payload) {
            if (payload && payload.chat && payload.chat.id) {
                storeSeenChatId(payload.chat.id);
            }
            state.isOpen = Boolean(payload.chat.is_open);
            if (payload.chat && payload.chat.viewer_role) {
                state.viewerRole = payload.chat.viewer_role;
            }

            shellEl.dataset.chatLoaded = "1";
            updateSummary(payload);
            renderMessages(payload);
            updateActions(payload.chat && payload.chat.decision ? payload.chat.decision : null);

            if (typeof CustomEvent === "function") {
                shellEl.dispatchEvent(
                    new CustomEvent("indyhub:bp-chat-state", {
                        bubbles: true,
                        detail: {
                            requestId: shellEl.dataset.requestId || null,
                            chat: payload.chat || null,
                            decision:
                                payload.chat && payload.chat.decision
                                    ? payload.chat.decision
                                    : null,
                        },
                    })
                );
            }

            if (!payload.chat.can_send) {
                toggleForm(false);
                if (!payload.chat.is_open) {
                    showStatus(__("This chat has been closed."), "warning");
                }
            } else {
                toggleForm(true);
                showStatus(null);
            }
        }

        function onClosed() {
            stopPolling();
            resetStatus();
            clearMessages();
            inputEl.value = "";
            updateActions(null);
            state.fetchUrl = null;
            state.sendUrl = null;
            state.decisionUrl = null;
            state.lastDecision = null;
            state.actionSubmitting = false;
            state.pendingInitialScroll = false;
            state.isOpen = false;
            delete shellEl.dataset.chatLoaded;
        }

        function hideShell() {
            if (inlineMode) {
                shellEl.classList.remove("is-active");
                onClosed();
                return;
            }
            if (useBootstrap) {
                modal.hide();
                return;
            }
            shellEl.classList.remove("show");
            shellEl.style.display = "none";
            shellEl.setAttribute("aria-hidden", "true");
            document.body.classList.remove("modal-open");
            document.body.style.overflow = previousBodyOverflow;
            previousBodyOverflow = "";
            removeBackdrop();
            onClosed();
        }

        function fetchChat() {
            if (!state.fetchUrl) {
                return Promise.reject(new Error(__("Missing chat URL")));
            }
            return fetch(withViewerRole(state.fetchUrl, state.viewerRole), {
                method: "GET",
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            })
                .then(function (res) {
                    if (!res.ok) {
                        throw new Error(__("Unable to load chat"));
                    }
                    return res.json();
                })
                .then(function (data) {
                    applyChatState(data);
                    return data;
                })
                .catch(function (err) {
                    showStatus(
                        err.message || __("Unable to load chat history."),
                        "error"
                    );
                    throw err;
                });
        }

        function startPolling() {
            stopPolling();
            if (!state.isOpen) {
                return;
            }
            state.polling = window.setInterval(function () {
                fetchChat().catch(function () {
                    stopPolling();
                });
            }, CHAT_POLL_INTERVAL_MS);
        }

        function submitDecision(decisionValue, extraPayload) {
            if (!state.decisionUrl || state.actionSubmitting) {
                return;
            }
            setActionSubmitting(true);
            if (
                actionStatusEl &&
                state.lastDecision &&
                state.lastDecision.pending_label
            ) {
                actionStatusEl.textContent = state.lastDecision.pending_label;
                actionStatusEl.classList.remove(
                    "text-danger",
                    "text-warning",
                    "text-success",
                    "text-primary"
                );
                actionStatusEl.classList.add("text-muted");
            }

            var decisionPayload = Object.assign(
                { decision: decisionValue },
                extraPayload || {}
            );
            if (state.viewerRole) {
                decisionPayload.viewer_role = state.viewerRole;
            }

            fetch(withViewerRole(state.decisionUrl, state.viewerRole), {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken(),
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
                body: JSON.stringify(decisionPayload),
            })
                .then(function (res) {
                    if (!res.ok) {
                        return res
                            .json()
                            .catch(function () {
                                throw new Error(__("Unable to update decision."));
                            })
                            .then(function (data) {
                                throw new Error(
                                    (data && data.error) ||
                                        __("Unable to update decision.")
                                );
                            });
                    }
                    return res.json().catch(function () {
                        return {};
                    });
                })
                .then(function (result) {
                    if (result && result.request_closed) {
                        showStatus(__("This request has been closed."), "warning");
                        state.isOpen = false;
                        stopPolling();
                        toggleForm(false);
                        updateActions(null);
                        return null;
                    }
                    return fetchChat();
                })
                .catch(function (err) {
                    showStatus(
                        err.message || __("Unable to update decision."),
                        "error"
                    );
                })
                .finally(function () {
                    setActionSubmitting(false);
                    if (!state.lastDecision) {
                        updateActions(null);
                    } else {
                        updateActions(state.lastDecision);
                    }
                });
        }

        function openChat(trigger) {
            state.fetchUrl = trigger.dataset.chatFetchUrl;
            state.sendUrl = trigger.dataset.chatSendUrl;
            state.typeName = trigger.dataset.chatTypeName || "";
            state.typeId = trigger.dataset.chatTypeId || null;
            if (trigger.dataset.chatRole) {
                state.viewerRole = trigger.dataset.chatRole;
            }
            state.pendingInitialScroll = !state.initialScrollDone;

            showStatus(__("Loading conversation..."), "info");
            toggleForm(false);
            clearMessages();
            updateActions(null);
            state.actionSubmitting = false;
            stopPolling();
            showShell();

            fetchChat()
                .then(function () {
                    startPolling();
                    scrollMessagesToBottom(messageContainer);
                })
                .catch(function () {
                    state.isOpen = false;
                });
        }

        if (useBootstrap) {
            shellEl.addEventListener("hidden.bs.modal", onClosed);
            shellEl.addEventListener("shown.bs.modal", function () {
                if (state.pendingInitialScroll) {
                    scrollMessagesToBottom(messageContainer);
                    state.pendingInitialScroll = false;
                }
            });
        } else if (!inlineMode) {
            shellEl.addEventListener("click", function (event) {
                var dismissTrigger = event.target.closest("[data-bs-dismiss='modal']");
                if (dismissTrigger) {
                    event.preventDefault();
                    hideShell();
                    return;
                }
                if (event.target === shellEl) {
                    hideShell();
                }
            });
            shellEl.addEventListener("keydown", function (event) {
                if (event.key === "Escape") {
                    hideShell();
                }
            });
            document.addEventListener("keydown", function (event) {
                if (event.key === "Escape" && shellEl.classList.contains("show")) {
                    hideShell();
                }
            });
        }

        shellEl.addEventListener("indyhub:chat-close", function () {
            hideShell();
        });

        formEl.addEventListener("submit", function (event) {
            event.preventDefault();
            if (!state.sendUrl) {
                return;
            }

            var message = String(inputEl.value || "").trim();
            if (!message) {
                return;
            }

            toggleForm(false);
            var sendPayload = { message: message };
            if (state.viewerRole) {
                sendPayload.viewer_role = state.viewerRole;
            }

            fetch(state.sendUrl, {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken(),
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
                body: JSON.stringify(sendPayload),
            })
                .then(function (res) {
                    if (!res.ok) {
                        return res
                            .json()
                            .then(function (data) {
                                throw new Error(
                                    (data && data.error) ||
                                        __("Message failed to send.")
                                );
                            })
                            .catch(function () {
                                throw new Error(__("Message failed to send."));
                            });
                    }
                    return res.json();
                })
                .then(function () {
                    inputEl.value = "";
                    toggleForm(true);
                    return fetchChat();
                })
                .catch(function (err) {
                    toggleForm(true);
                    showStatus(
                        err.message || __("Message failed to send."),
                        "error"
                    );
                });
        });

        if (proposalFormEl) {
            proposalFormEl.addEventListener("submit", function (event) {
                event.preventDefault();
                if (
                    !state.lastDecision ||
                    !state.lastDecision.viewer_can_propose ||
                    state.actionSubmitting ||
                    !proposalInputEl
                ) {
                    return;
                }

                var amount = String(proposalInputEl.value || "").trim();
                if (!amount) {
                    showStatus(__("Enter an amount in ISK."), "warning");
                    proposalInputEl.focus();
                    return;
                }

                submitDecision("propose", { amount: amount });
            });
        }

        if (acceptBtnEl) {
            acceptBtnEl.addEventListener("click", function () {
                if (
                    !state.lastDecision ||
                    !state.lastDecision.viewer_can_accept ||
                    state.actionSubmitting
                ) {
                    return;
                }
                submitDecision("accept");
            });
        }

        updateActions(null);

        return {
            id: shellEl.id,
            inlineMode: inlineMode,
            element: shellEl,
            open: openChat,
            close: hideShell,
        };
    }

    function init() {
        var shellNodes = Array.prototype.slice.call(
            document.querySelectorAll("[data-chat-modal]")
        );
        if (!shellNodes.length) {
            debugLog("[IndyHub] No chat shell found on page");
            return;
        }

        var controllersById = Object.create(null);
        var defaultController = null;

        shellNodes.forEach(function (shellEl, index) {
            if (!shellEl.id) {
                shellEl.id = "bpChatShell" + String(index + 1);
            }
            var controller = createChatController(shellEl);
            if (!controller) {
                return;
            }
            controllersById[shellEl.id] = controller;
            if (!defaultController) {
                defaultController = controller;
            }
        });

        function getController(trigger) {
            var targetId = trigger.getAttribute("data-chat-target");
            if (targetId && controllersById[targetId]) {
                return controllersById[targetId];
            }
            return defaultController;
        }

        function closeOtherControllers(activeController) {
            Object.keys(controllersById).forEach(function (controllerId) {
                var controller = controllersById[controllerId];
                if (!controller || controller === activeController) {
                    return;
                }
                if (controller.inlineMode) {
                    controller.close();
                }
            });
        }

        document.addEventListener("click", function (event) {
            var trigger = event.target.closest(".bp-chat-trigger");
            if (!trigger) {
                return;
            }
            event.preventDefault();
            var controller = getController(trigger);
            if (!controller) {
                return;
            }
            closeOtherControllers(controller);
            debugLog(
                "[IndyHub] Opening chat",
                trigger.dataset.chatFetchUrl,
                trigger.dataset.chatSendUrl
            );
            controller.open(trigger);
        });

        var autoOpenRoot = document.querySelector("[data-auto-open-chat]");
        if (autoOpenRoot) {
            var autoChatId = autoOpenRoot.dataset.autoOpenChat;
            if (autoChatId) {
                var attemptAutoOpen = function () {
                    var selector =
                        '.bp-chat-trigger[data-chat-id="' + autoChatId + '"]';
                    var autoTrigger = document.querySelector(selector);
                    if (!autoTrigger) {
                        return false;
                    }
                    autoTrigger.click();
                    autoOpenRoot.dataset.autoOpenChat = "";
                    try {
                        var currentUrl = new URL(window.location.href);
                        if (currentUrl.searchParams.has("open_chat")) {
                            currentUrl.searchParams.delete("open_chat");
                            window.history.replaceState(
                                {},
                                document.title,
                                currentUrl.toString()
                            );
                        }
                    } catch (err) {
                        debugLog(
                            "[IndyHub] Unable to clean auto-open query param",
                            err
                        );
                    }
                    return true;
                };

                if (!attemptAutoOpen()) {
                    window.setTimeout(attemptAutoOpen, 200);
                }
            }
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
