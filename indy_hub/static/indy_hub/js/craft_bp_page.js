(function () {
    function parseJsonScript(id, fallback) {
        const el = document.getElementById(id);
        if (!el) {
            return fallback;
        }

        try {
            return JSON.parse(el.textContent);
        } catch (error) {
            console.error(`[IndyHub] Failed to parse ${id}`, error);
            return fallback;
        }
    }

    const payloadData = parseJsonScript('blueprint-payload', {});
    const pageConfig = payloadData.page || {};
    const pageMessages = pageConfig.messages || {};
    const pageUrls = pageConfig.urls || {};
    const blueprintUiState = {
        hydrated: false,
        initialized: false,
    };

    window.BLUEPRINT_DATA = Object.assign({}, payloadData, {
        save_url: payloadData.urls ? payloadData.urls.save : undefined,
        load_url: payloadData.urls ? payloadData.urls.load_list : undefined,
        load_config_url: payloadData.urls ? payloadData.urls.load_config : undefined,
        fuzzwork_price_url: payloadData.urls ? payloadData.urls.fuzzwork_price : undefined,
        page: pageConfig,
    });

    try {
        const params = new URLSearchParams(window.location.search);
        const href = String(window.location && window.location.href ? window.location.href : '');
        const enabled =
            params.get('indy_debug') === '1' ||
            params.get('debug') === '1' ||
            href.includes('indy_debug=1') ||
            href.includes('debug=1');

        if (enabled) {
            window.INDY_HUB_DEBUG = true;
            if (typeof console !== 'undefined' && typeof console.log === 'function') {
                console.log('[IndyHub] Debug enabled (INDY_HUB_DEBUG=true)');
            }
        }
    } catch (error) {
        // ignore malformed URLs
    }

    function getMessage(key, fallback) {
        const value = pageMessages[key];
        return typeof value === 'string' && value ? value : fallback;
    }

    function getCraftPageConfig() {
        return (window.BLUEPRINT_DATA && window.BLUEPRINT_DATA.page) || {};
    }

    function hydrateConfigurePane() {
        const pane = document.getElementById('configure-pane');
        if (!pane) {
            return false;
        }
        if (pane.dataset.configHydrated === 'true') {
            blueprintUiState.hydrated = true;
            return true;
        }

        const template = document.getElementById('configure-pane-template');
        if (!template) {
            return false;
        }

        const notice = document.getElementById('configurePaneLazyNotice');
        if (notice) {
            notice.remove();
        }

        pane.appendChild(template.content.cloneNode(true));
        template.remove();
        pane.dataset.configHydrated = 'true';
        blueprintUiState.hydrated = true;
        return true;
    }

    function initializeBlueprintUi() {
        if (!hydrateConfigurePane()) {
            return false;
        }
        if (blueprintUiState.initialized) {
            return true;
        }

        bindBulkActions();
        bindPresetButtons();
        bindConfigAccordion();
        bindBlueprintSearch();
        if (typeof window.applyDeferredCraftBlueprintInputState === 'function') {
            window.applyDeferredCraftBlueprintInputState();
        }
        blueprintUiState.initialized = true;
        return true;
    }

    function updateConfigTabFromState() {
        if (!initializeBlueprintUi()) {
            return false;
        }
        validateBlueprintRuns();
        applyBlueprintCardFilters();
        updateQuickStats();
        return true;
    }

    window.updateConfigTabFromState = updateConfigTabFromState;

    function getBlueprintConfigsByTypeId() {
        return new Map(
            (Array.isArray(getCraftPageConfig().blueprint_configs) ? getCraftPageConfig().blueprint_configs : []).map(function (bp) {
                return [Number(bp.type_id) || 0, bp];
            }).filter(function ([typeId]) {
                return typeId > 0;
            })
        );
    }

    function getActiveBlueprintProductTypeIds() {
        const activeProductTypeIds = new Set();
        const cyclesSummary = window.getCraftProductionCyclesSummary && typeof window.getCraftProductionCyclesSummary === 'function'
            ? window.getCraftProductionCyclesSummary() || {}
            : (getCraftPageConfig().craft_cycles_summary_static || {});

        Object.values(cyclesSummary || {}).forEach(function (entry) {
            const typeId = Number(entry?.type_id || entry?.typeId || 0) || 0;
            const cycles = Number(entry?.cycles || 0) || 0;
            if (typeId > 0 && cycles > 0) {
                activeProductTypeIds.add(typeId);
            }
        });

        return activeProductTypeIds;
    }

    function updateBlueprintEmptyState(totalVisible, searchTerm) {
        const accordion = document.getElementById('blueprintConfigAccordion');
        const container = accordion ? accordion.parentElement : null;
        if (!container) {
            return;
        }

        const existing = document.getElementById('blueprintNoResults');
        if (totalVisible > 0) {
            if (existing) {
                existing.remove();
            }
            return;
        }

        const message = searchTerm
            ? getMessage('no_blueprints_match_search', 'No blueprints match your search')
            : getMessage('no_blueprints_used_current_project', 'No blueprints are currently used by this project state.');

        if (existing) {
            existing.innerHTML = `<i class="fas fa-search me-2"></i>${message}`;
            return;
        }

        const emptyState = document.createElement('div');
        emptyState.id = 'blueprintNoResults';
        emptyState.className = 'alert alert-info mt-3';
        emptyState.innerHTML = `<i class="fas fa-search me-2"></i>${message}`;
        container.appendChild(emptyState);
    }

    function applyBlueprintCardFilters() {
        const accordion = document.getElementById('blueprintConfigAccordion');
        if (!accordion) {
            return 0;
        }

        const blueprintConfigsByTypeId = getBlueprintConfigsByTypeId();
        const activeProductTypeIds = getActiveBlueprintProductTypeIds();
        const searchInput = document.getElementById('blueprintSearchInput');
        const searchTerm = String(searchInput?.value || '').trim().toLowerCase();
        let totalVisible = 0;

        accordion.querySelectorAll('.accordion-item').forEach(function (item) {
            const groupName = item.querySelector('.accordion-header')?.textContent?.toLowerCase() || '';
            const cards = item.querySelectorAll('.craft-bp-card, .craft-config-item');
            let visibleInGroup = 0;

            cards.forEach(function (card) {
                const blueprintTypeId = Number(card.getAttribute('data-blueprint-type-id') || card.getAttribute('data-type-id') || 0) || 0;
                const blueprintConfig = blueprintConfigsByTypeId.get(blueprintTypeId) || null;
                const productTypeId = Number(
                    card.getAttribute('data-product-type-id')
                    || blueprintConfig?.product_type_id
                    || blueprintConfig?.productTypeId
                    || 0
                ) || 0;
                const usedInProject = productTypeId > 0 ? activeProductTypeIds.has(productTypeId) : true;
                const itemName =
                    card.querySelector('.card-title')?.textContent?.toLowerCase()
                    || card.querySelector('.craft-config-name')?.textContent?.toLowerCase()
                    || '';
                const matchesSearch = searchTerm === '' || groupName.includes(searchTerm) || itemName.includes(searchTerm);
                const shouldShow = usedInProject && matchesSearch;

                card.dataset.usageHidden = usedInProject ? 'false' : 'true';
                card.style.display = shouldShow ? '' : 'none';
                if (shouldShow) {
                    visibleInGroup += 1;
                    totalVisible += 1;
                }
            });

            item.style.display = visibleInGroup > 0 ? '' : 'none';
        });

        updateBlueprintEmptyState(totalVisible, searchTerm);
        return totalVisible;
    }

    function getCsrfToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    const EVE_JOB_LAUNCH_WINDOW_SECONDS = 30 * 24 * 60 * 60;

    function getProductionTimeMapEntries() {
        const timeMap = window.getCraftProductionTimeMap && typeof window.getCraftProductionTimeMap === 'function'
            ? window.getCraftProductionTimeMap()
            : (window.BLUEPRINT_DATA?.production_time_map || window.BLUEPRINT_DATA?.productionTimeMap || {});
        return Object.values(timeMap || {});
    }

    function computeEffectiveCycleSeconds(baseTimeSeconds, timeEfficiency) {
        const numericBaseTime = Math.max(0, Math.ceil(Number(baseTimeSeconds) || 0)) || 0;
        if (!(numericBaseTime > 0)) {
            return 0;
        }
        return Math.max(1, Math.ceil(numericBaseTime * Math.max(0, 1 - ((Number(timeEfficiency) || 0) / 100))));
    }

    function computeMaxRunsBeforeLaunchWindow(effectiveCycleSeconds) {
        const cycleSeconds = Math.max(0, Math.ceil(Number(effectiveCycleSeconds) || 0)) || 0;
        if (!(cycleSeconds > 0)) {
            return null;
        }
        return Math.max(1, Math.ceil(EVE_JOB_LAUNCH_WINDOW_SECONDS / cycleSeconds));
    }

    function getCopyRequestLaunchLimit(typeId, productTypeId, timeEfficiency) {
        const numericTypeId = Number(typeId || 0) || 0;
        const numericProductTypeId = Number(productTypeId || 0) || 0;
        const timeEntry = getProductionTimeMapEntries().find(function (entry) {
            const entryBlueprintTypeId = Number(entry?.blueprint_type_id || entry?.blueprintTypeId || 0) || 0;
            const entryTypeId = Number(entry?.type_id || entry?.typeId || 0) || 0;
            return (entryBlueprintTypeId > 0 && entryBlueprintTypeId === numericTypeId)
                || (entryTypeId > 0 && entryTypeId === numericProductTypeId);
        });

        const effectiveCycleSeconds = computeEffectiveCycleSeconds(
            Number(timeEntry?.base_time_seconds || timeEntry?.baseTimeSeconds || 0) || 0,
            Number(timeEfficiency) || 0,
        );
        const maxRunsPerCopy = computeMaxRunsBeforeLaunchWindow(effectiveCycleSeconds);

        return {
            baseTimeSeconds: Number(timeEntry?.base_time_seconds || timeEntry?.baseTimeSeconds || 0) || 0,
            effectiveCycleSeconds,
            maxRunsPerCopy,
        };
    }

    function updateBlueprintEmptyState(totalVisible, searchTerm) {
        const accordion = document.getElementById('blueprintConfigAccordion');
        const container = accordion ? accordion.parentElement : null;
        if (!container) {
            return;
        }

        const existing = document.getElementById('blueprintNoResults');
        if (totalVisible > 0) {
            if (existing) {
                existing.remove();
            }
            return;
        }

        const message = searchTerm
            ? getMessage('no_blueprints_match_search', 'No blueprints match your search')
            : getMessage('no_blueprints_used_current_project', 'No blueprints are currently used by this project state.');

        if (existing) {
            existing.innerHTML = `<i class="fas fa-search me-2"></i>${message}`;
            return;
        }

        const emptyState = document.createElement('div');
        emptyState.id = 'blueprintNoResults';
        emptyState.className = 'alert alert-info mt-3';
        emptyState.innerHTML = `<i class="fas fa-search me-2"></i>${message}`;
        container.appendChild(emptyState);
    }

    function applyBlueprintCardFilters() {
        const accordion = document.getElementById('blueprintConfigAccordion');
        if (!accordion) {
            return 0;
        }

        const blueprintConfigsByTypeId = getBlueprintConfigsByTypeId();
        const activeProductTypeIds = getActiveBlueprintProductTypeIds();
        const searchInput = document.getElementById('blueprintSearchInput');
        const searchTerm = String(searchInput?.value || '').trim().toLowerCase();
        let totalVisible = 0;

        accordion.querySelectorAll('.accordion-item').forEach(function (item) {
            const groupName = item.querySelector('.accordion-header')?.textContent?.toLowerCase() || '';
            const cards = item.querySelectorAll('.craft-bp-card, .craft-config-item');
            let visibleInGroup = 0;

            cards.forEach(function (card) {
                const blueprintTypeId = Number(card.getAttribute('data-blueprint-type-id') || card.getAttribute('data-type-id') || 0) || 0;
                const blueprintConfig = blueprintConfigsByTypeId.get(blueprintTypeId) || null;
                const productTypeId = Number(
                    card.getAttribute('data-product-type-id')
                    || blueprintConfig?.product_type_id
                    || blueprintConfig?.productTypeId
                    || 0
                ) || 0;
                const usedInProject = productTypeId > 0 ? activeProductTypeIds.has(productTypeId) : true;
                const itemName =
                    card.querySelector('.card-title')?.textContent?.toLowerCase()
                    || card.querySelector('.craft-config-name')?.textContent?.toLowerCase()
                    || '';
                const matchesSearch = searchTerm === '' || groupName.includes(searchTerm) || itemName.includes(searchTerm);
                const shouldShow = usedInProject && matchesSearch;

                card.dataset.usageHidden = usedInProject ? 'false' : 'true';
                card.style.display = shouldShow ? '' : 'none';
                if (shouldShow) {
                    visibleInGroup += 1;
                    totalVisible += 1;
                }
            });

            item.style.display = visibleInGroup > 0 ? '' : 'none';
        });

        updateBlueprintEmptyState(totalVisible, searchTerm);
        return totalVisible;
    }

    function getCsrfToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function getSelectedCopyEfficiencies(typeId, fallbackTE) {
        const select = document.getElementById(`bpCopySelect${typeId}`);
        if (!select) {
            return { me: 0, te: Number(fallbackTE) || 0 };
        }

        const [meValue, teValue] = String(select.value || '').split(',');
        return {
            me: Number(meValue) || 0,
            te: Number(teValue) || Number(fallbackTE) || 0,
        };
    }

    function updateBlueprintCopyRequestRecommendation(bp, shortfallRuns) {
        const typeId = Number(bp?.type_id || 0) || 0;
        if (!(typeId > 0)) {
            return;
        }

        const selectedEfficiency = getSelectedCopyEfficiencies(typeId, bp?.time_efficiency);
        const launchLimit = getCopyRequestLaunchLimit(typeId, bp?.product_type_id, selectedEfficiency.te);
        const maxRunsPerCopy = Number(launchLimit.maxRunsPerCopy || 0) || 0;
        const recommendedRunsPerCopy = maxRunsPerCopy > 0
            ? Math.min(Math.max(1, shortfallRuns), maxRunsPerCopy)
            : Math.max(1, shortfallRuns);
        const recommendedCopies = Math.max(1, Math.ceil(Math.max(1, shortfallRuns) / Math.max(1, recommendedRunsPerCopy)));

        const alertContainer = document.querySelector(`.runs-validation-alert[data-bp-type-id="${typeId}"]`);
        const requestContainer = document.querySelector(`.blueprint-sharing-section[data-bp-type-id="${typeId}"]`);
        const runsInput = document.getElementById(`bpRunsRequested${typeId}`);
        const copiesInput = document.getElementById(`bpCopiesRequested${typeId}`);

        if (runsInput && maxRunsPerCopy > 0) {
            runsInput.max = String(maxRunsPerCopy);
        }

        if (runsInput) {
            const currentRuns = Math.max(1, parseInt(runsInput.value || '0', 10) || 0);
            const shouldAutofillRuns = !runsInput.dataset.userModified || currentRuns === 300 || (maxRunsPerCopy > 0 && currentRuns > maxRunsPerCopy);
            if (shouldAutofillRuns) {
                runsInput.value = String(recommendedRunsPerCopy);
                runsInput.dataset.autoFilled = 'true';
            }
        }

        if (copiesInput) {
            const currentCopies = Math.max(1, parseInt(copiesInput.value || '0', 10) || 0);
            const shouldAutofillCopies = !copiesInput.dataset.userModified || currentCopies === 1 || (runsInput && (Number(runsInput.value) || 0) * currentCopies < shortfallRuns);
            if (shouldAutofillCopies) {
                copiesInput.value = String(recommendedCopies);
                copiesInput.dataset.autoFilled = 'true';
            }
        }

        if (requestContainer) {
            requestContainer.dataset.maxRunsPerCopy = maxRunsPerCopy > 0 ? String(maxRunsPerCopy) : '';
            requestContainer.dataset.recommendedRunsPerCopy = String(recommendedRunsPerCopy);
            requestContainer.dataset.recommendedCopies = String(recommendedCopies);
            requestContainer.dataset.requiredRunsShortfall = String(shortfallRuns);
        }

        if (alertContainer) {
            const guidanceHtml = maxRunsPerCopy > 0
                ? `<div class="small text-muted mt-1">${getMessage('copy_request_guidance', 'Recommended request')}: <strong>${recommendedCopies}</strong> ${getMessage('copies_label', 'copies')} x <strong>${recommendedRunsPerCopy}</strong> ${getMessage('runs_label', 'runs')}</div><div class="small text-muted">${getMessage('copy_request_cap', 'Max per copy')}: ${maxRunsPerCopy} ${getMessage('runs_label', 'runs')}</div>`
                : '';
            alertContainer.innerHTML = `
                <div class="alert alert-warning py-1 px-2 mb-2 text-center" style="font-size: 0.8rem;">
                    <i class="fas fa-exclamation-triangle me-1"></i>
                    <strong>Missing ${shortfallRuns} run(s)</strong>
                    ${guidanceHtml}
                </div>
            `;
        }
    }

    function preparePageNavigationLoading(message) {
        if (window.CraftBPLoading && typeof window.CraftBPLoading.show === 'function') {
            try {
                window.CraftBPLoading.show({
                    message: message || getMessage('preparing_workspace', 'Preparing production workspace'),
                });
            } catch (error) {
                console.warn('[IndyHub] Failed to show navigation loading state', error);
            }
        }
    }

    function handleDeferredShellHydration() {
        const payload = window.BLUEPRINT_DATA || {};
        if (!payload.deferred_shell) {
            return false;
        }

        const hydrateUrl = payload.hydrate_url;
        const messageEl = document.getElementById('craft-bp-loading-message');

        if (!hydrateUrl) {
            if (messageEl) {
                messageEl.textContent = getMessage('unable_to_prepare_workspace', 'Unable to prepare production workspace.');
            }
            return true;
        }

        fetch(hydrateUrl, {
            credentials: 'same-origin',
            headers: {
                Accept: 'text/html',
                'X-Requested-With': 'XMLHttpRequest',
            },
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error(`Hydration failed with status ${response.status}`);
                }
                return response.text();
            })
            .then(function (html) {
                document.open();
                document.write(html);
                document.close();
            })
            .catch(function (error) {
                console.error('[IndyHub] Failed to hydrate craft workspace', error);
                if (messageEl) {
                    messageEl.textContent = getMessage(
                        'failed_to_load_workspace_retry',
                        'Failed to load production workspace. Retrying direct page load...'
                    );
                }
                window.location.replace(hydrateUrl);
            });

        return true;
    }

    function requestBlueprintCopy(typeId) {
        const select = document.getElementById(`bpCopySelect${typeId}`);
        if (!select) {
            return;
        }

        const [me, te] = String(select.value || '').split(',');
        const requestContainer = document.querySelector(`.blueprint-sharing-section[data-bp-type-id="${typeId}"]`);
        const maxRunsPerCopy = Math.max(0, parseInt(requestContainer?.dataset.maxRunsPerCopy || '0', 10) || 0);
        const recommendedRunsPerCopy = Math.max(1, parseInt(requestContainer?.dataset.recommendedRunsPerCopy || '1', 10) || 1);
        let runsValue = Math.max(1, parseInt(document.getElementById(`bpRunsRequested${typeId}`)?.value || '300', 10) || 300);
        const copiesValue = Math.max(1, parseInt(document.getElementById(`bpCopiesRequested${typeId}`)?.value || '1', 10) || 1);
        const action = pageUrls.bp_copy_request_create;
        if (!action) {
            return;
        }

        if (maxRunsPerCopy > 0 && runsValue > maxRunsPerCopy) {
            runsValue = recommendedRunsPerCopy;
            const runsInput = document.getElementById(`bpRunsRequested${typeId}`);
            if (runsInput) {
                runsInput.value = String(runsValue);
            }
            window.alert(
                `${getMessage('copy_request_limit_prefix', 'This blueprint is limited to')} ${maxRunsPerCopy} ${getMessage('runs_per_copy_suffix', 'runs per copy')} (TE${te || 0}).`
            );
            return;
        }

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = action;

        [
            ['csrfmiddlewaretoken', getCsrfToken()],
            ['type_id', String(typeId)],
            ['material_efficiency', me || '0'],
            ['time_efficiency', te || '0'],
            ['runs_requested', String(runsValue)],
            ['copies_requested', String(copiesValue)],
        ].forEach(function ([name, value]) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = name;
            input.value = value;
            form.appendChild(input);
        });

        document.body.appendChild(form);
        preparePageNavigationLoading(getMessage('loading_workspace', 'Loading workspace...'));
        form.submit();
    }

    function bindNavigationLoading() {
        function hasUnsavedProjectWorkspaceChanges() {
            return Boolean(
                window.CraftBPProjectWorkspace
                && typeof window.CraftBPProjectWorkspace.hasUnsavedChanges === 'function'
                && window.CraftBPProjectWorkspace.hasUnsavedChanges()
            );
        }

        window.addEventListener('beforeunload', function (event) {
            if (hasUnsavedProjectWorkspaceChanges()) {
                event.preventDefault();
                event.returnValue = '';
                return '';
            }
            preparePageNavigationLoading(getMessage('loading_workspace', 'Loading workspace...'));
        });

        document.querySelectorAll('a[href]').forEach(function (link) {
            link.addEventListener('click', function (event) {
                if (event.defaultPrevented || event.button !== 0) return;
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                if (link.target && link.target !== '_self') return;
                if (link.hasAttribute('download')) return;

                const href = link.getAttribute('href') || '';
                if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;

                const targetUrl = new URL(href, window.location.href);
                if (targetUrl.origin !== window.location.origin) return;
                if (hasUnsavedProjectWorkspaceChanges()) return;

                preparePageNavigationLoading(getMessage('loading_workspace', 'Loading workspace...'));
            });
        });
    }

    function bindControlForm() {
        const controlForm = document.getElementById('blueprint-control-form');
        const activeTabInput = document.getElementById('activeTabInput');

        if (!controlForm || !activeTabInput) {
            return;
        }

        controlForm.addEventListener('submit', function (event) {
            const activeTab = document.querySelector('#bpTabs .nav-link.active');
            if (activeTab) {
                const target = activeTab.getAttribute('data-bs-target');
                if (target) {
                    activeTabInput.value = target.replace('#tab-', '');
                }
            }

            if (window.CraftBP && typeof window.CraftBP.recalculate === 'function') {
                event.preventDefault();
                const submitBtn = controlForm.querySelector('button[type="submit"]');
                if (submitBtn) {
                    submitBtn.disabled = true;
                }

                window.CraftBP.recalculate({
                    activeTab: activeTabInput.value || 'materials',
                }).catch(function () {
                    preparePageNavigationLoading(getMessage('loading_workspace', 'Loading workspace...'));
                    controlForm.submit();
                }).finally(function () {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                    }
                });
            }
        });
    }

    function bindMaterialsGroups() {
        document.addEventListener('click', function (event) {
            const toggle = event.target.closest('[data-craft-group-toggle]');
            if (toggle) {
                const card = toggle.closest('.craft-group-card');
                if (card) {
                    card.classList.toggle('collapsed');
                }
                return;
            }

            const copyButton = event.target.closest('[data-request-blueprint-copy]');
            if (copyButton) {
                const typeId = Number(copyButton.getAttribute('data-request-blueprint-copy'));
                if (typeId > 0) {
                    requestBlueprintCopy(typeId);
                }
            }
        });

        const expandMaterialsBtn = document.getElementById('expand-materials');
        const collapseMaterialsBtn = document.getElementById('collapse-materials');

        if (expandMaterialsBtn) {
            expandMaterialsBtn.addEventListener('click', function () {
                document.querySelectorAll('.craft-group-card').forEach(function (card) {
                    card.classList.remove('collapsed');
                });
            });
        }

        if (collapseMaterialsBtn) {
            collapseMaterialsBtn.addEventListener('click', function () {
                document.querySelectorAll('.craft-group-card').forEach(function (card) {
                    card.classList.add('collapsed');
                });
            });
        }

        if (document.body && document.body.dataset.blueprintRecommendationInputBound !== 'true') {
            document.addEventListener('input', function (event) {
                const target = event.target;
                if (!target || !target.id || !/^(bpCopySelect|bpRunsRequested|bpCopiesRequested)\d+$/.test(target.id)) {
                    return;
                }

                target.dataset.userModified = 'true';
                const match = target.id.match(/(\d+)$/);
                const typeId = match ? Number(match[1]) : 0;
                const config = (getCraftPageConfig().blueprint_configs || []).find(function (bp) {
                    return Number(bp?.type_id || 0) === typeId;
                });
                if (!config) {
                    return;
                }
                const productId = config.product_type_id || config.type_id;
                const cyclesSummary = window.getCraftProductionCyclesSummary && typeof window.getCraftProductionCyclesSummary === 'function'
                    ? window.getCraftProductionCyclesSummary() || {}
                    : (getCraftPageConfig().craft_cycles_summary_static || {});
                const cyclesData = cyclesSummary[productId] || cyclesSummary[String(productId)];
                const ownedRuns = config.user_owns && config.is_copy && config.runs_available != null
                    ? Number(config.runs_available) || 0
                    : 0;
                const calculatedCycles = cyclesData ? Number(cyclesData.cycles) || 0 : 0;
                if (calculatedCycles > ownedRuns) {
                    updateBlueprintCopyRequestRecommendation(config, calculatedCycles - ownedRuns);
                }
            }, true);
            document.body.dataset.blueprintRecommendationInputBound = 'true';
        }
    }

    function showToast(message, isSuccess) {
        const bulkToast = document.getElementById('bulkActionToast');
        const toastMessage = document.getElementById('toastMessage');
        if (!bulkToast || !toastMessage) {
            return;
        }

        toastMessage.textContent = message;
        bulkToast.classList.remove('bg-success', 'bg-danger');
        bulkToast.classList.add(isSuccess ? 'bg-success' : 'bg-danger');
        const toast = new window.bootstrap.Toast(bulkToast);
        toast.show();
    }

    function bindBulkActions() {
        const applyBulkMEBtn = document.getElementById('applyBulkME');
        const applyBulkTEBtn = document.getElementById('applyBulkTE');

        if (applyBulkMEBtn) {
            applyBulkMEBtn.addEventListener('click', function () {
                const value = Number(document.getElementById('bulkMEValue')?.value);
                if (value < 0 || value > 10) {
                    showToast(getMessage('me_range_error', 'ME must be between 0 and 10'), false);
                    return;
                }
                let count = 0;
                document.querySelectorAll('.bp-me-input').forEach(function (input) {
                    input.value = value;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    count += 1;
                });
                showToast(`${getMessage('applied_me', 'Applied ME')} ${value} ${getMessage('to', 'to')} ${count} ${getMessage('blueprints', 'blueprints')}`, true);
            });
        }

        if (applyBulkTEBtn) {
            applyBulkTEBtn.addEventListener('click', function () {
                const value = Number(document.getElementById('bulkTEValue')?.value);
                if (value < 0 || value > 20) {
                    showToast(getMessage('te_range_error', 'TE must be between 0 and 20'), false);
                    return;
                }
                let count = 0;
                document.querySelectorAll('.bp-te-input').forEach(function (input) {
                    input.value = value;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    count += 1;
                });
                showToast(`${getMessage('applied_te', 'Applied TE')} ${value} ${getMessage('to', 'to')} ${count} ${getMessage('blueprints', 'blueprints')}`, true);
            });
        }
    }

    function bindPresetButtons() {
        const presetMaxBtn = document.getElementById('presetMaxEfficiency');
        const presetOwnedBtn = document.getElementById('presetOwnedEfficiency');
        const presetResetBtn = document.getElementById('presetResetEfficiency');
        const blueprintConfigs = getCraftPageConfig().blueprint_configs || [];
        const blueprintConfigsByTypeId = new Map(
            (Array.isArray(blueprintConfigs) ? blueprintConfigs : []).map(function (bp) {
                return [Number(bp.type_id), bp];
            })
        );

        if (presetMaxBtn) {
            presetMaxBtn.addEventListener('click', function () {
                let count = 0;
                document.querySelectorAll('.bp-me-input').forEach(function (input) {
                    input.value = 10;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    count += 1;
                });
                document.querySelectorAll('.bp-te-input').forEach(function (input) {
                    input.value = 20;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                });
                showToast(`${getMessage('max_efficiency_applied', 'Max efficiency applied to')} ${count} ${getMessage('blueprints', 'blueprints')}`, true);
            });
        }

        if (presetOwnedBtn) {
            presetOwnedBtn.addEventListener('click', function () {
                let count = 0;
                document.querySelectorAll('.bp-me-input').forEach(function (input) {
                    const card = input.closest('.craft-bp-card');
                    const typeId = Number(card?.getAttribute('data-blueprint-type-id'));
                    const bp = blueprintConfigsByTypeId.get(typeId);
                    input.value = bp && bp.is_owned && bp.user_material_efficiency != null ? bp.user_material_efficiency : 0;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    count += 1;
                });
                document.querySelectorAll('.bp-te-input').forEach(function (input) {
                    const card = input.closest('.craft-bp-card');
                    const typeId = Number(card?.getAttribute('data-blueprint-type-id'));
                    const bp = blueprintConfigsByTypeId.get(typeId);
                    input.value = bp && bp.is_owned && bp.user_time_efficiency != null ? bp.user_time_efficiency : 0;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                });
                showToast(`${getMessage('applied_owned_bp_parameters', 'Applied owned BP parameters to')} ${count} ${getMessage('blueprints', 'blueprints')}`, true);
            });
        }

        if (presetResetBtn) {
            presetResetBtn.addEventListener('click', function () {
                let count = 0;
                document.querySelectorAll('.bp-me-input').forEach(function (input) {
                    input.value = 0;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    count += 1;
                });
                document.querySelectorAll('.bp-te-input').forEach(function (input) {
                    input.value = 0;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                });
                showToast(`${getMessage('reset', 'Reset')} ${count} ${getMessage('blueprints_default_values', 'blueprints to default values')}`, true);
            });
        }
    }

    function bindConfigAccordion() {
        const expandAllConfigBtn = document.getElementById('expandAllConfig');
        const collapseAllConfigBtn = document.getElementById('collapseAllConfig');

        if (expandAllConfigBtn) {
            expandAllConfigBtn.addEventListener('click', function () {
                if (!window.bootstrap || !window.bootstrap.Collapse) {
                    return;
                }
                document.querySelectorAll('#blueprintConfigAccordion .accordion-collapse').forEach(function (element) {
                    window.bootstrap.Collapse.getOrCreateInstance(element, { toggle: false }).show();
                });
            });
        }

        if (collapseAllConfigBtn) {
            collapseAllConfigBtn.addEventListener('click', function () {
                if (!window.bootstrap || !window.bootstrap.Collapse) {
                    return;
                }
                document.querySelectorAll('#blueprintConfigAccordion .accordion-collapse.show').forEach(function (element) {
                    window.bootstrap.Collapse.getOrCreateInstance(element, { toggle: false }).hide();
                });
            });
        }
    }

    function bindBlueprintSearch() {
        const searchInput = document.getElementById('blueprintSearchInput');
        const clearSearchBtn = document.getElementById('clearSearch');
        if (!searchInput) {
            return;
        }

        function filterBlueprints(query) {
            const searchTerm = String(query || '').toLowerCase();
            let totalVisible = 0;

            document.querySelectorAll('#blueprintConfigAccordion .accordion-item').forEach(function (item) {
                const groupName = item.querySelector('.accordion-button')?.textContent?.toLowerCase() || '';
                const configs = item.querySelectorAll('.craft-bp-card, .craft-config-item');
                let visibleInGroup = 0;

                configs.forEach(function (config) {
                    const itemName =
                        config.querySelector('.card-title')?.textContent?.toLowerCase() ||
                        config.querySelector('.craft-config-name')?.textContent?.toLowerCase() ||
                        '';
                    const matches = searchTerm === '' || groupName.includes(searchTerm) || itemName.includes(searchTerm);
                    config.style.display = matches ? '' : 'none';
                    if (matches) {
                        visibleInGroup += 1;
                        totalVisible += 1;
                    }
                });

                item.style.display = visibleInGroup > 0 ? '' : 'none';
            });

            const noResultsMsg = document.getElementById('blueprintNoResults');
            if (totalVisible === 0 && searchTerm !== '') {
                if (!noResultsMsg) {
                    const msg = document.createElement('div');
                    msg.id = 'blueprintNoResults';
                    msg.className = 'alert alert-info mt-3';
                    msg.innerHTML = `<i class="fas fa-search me-2"></i>${getMessage('no_blueprints_match_search', 'No blueprints match your search')}`;
                    document.querySelector('#blueprintConfigAccordion')?.parentElement?.appendChild(msg);
                }
            } else if (noResultsMsg) {
                noResultsMsg.remove();
            }
        }

        searchInput.addEventListener('input', function () {
            filterBlueprints(this.value);
        });

        if (clearSearchBtn) {
            clearSearchBtn.addEventListener('click', function () {
                searchInput.value = '';
                filterBlueprints('');
                searchInput.focus();
            });
        }
    }

    function bindCsvExports() {
        function exportTableToCSV(tableId, filename) {
            const table = document.querySelector(tableId);
            if (!table) {
                return;
            }

            const rows = Array.from(table.querySelectorAll('tr')).filter(function (row) {
                return row.getAttribute('data-export-skip') !== 'true' && !row.hidden;
            });
            const csv = rows.map(function (row) {
                const cells = Array.from(row.querySelectorAll('th, td'));
                return cells.map(function (cell) {
                    const text = cell.textContent.trim().replace(/"/g, '""');
                    return `"${text}"`;
                }).join(',');
            }).join('\n');

            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filename;
            link.click();
        }

        document.getElementById('exportPurchaseCSV')?.addEventListener('click', function () {
            exportTableToCSV('#financialItemsBody', 'purchase_planner.csv');
        });

        document.getElementById('exportShoppingCSV')?.addEventListener('click', function () {
            exportTableToCSV('#needed-table', 'shopping_list.csv');
        });

        document.getElementById('loadFuzzworkFromAlert')?.addEventListener('click', function () {
            document.getElementById('loadFuzzworkBtn')?.click();
        });
    }

    function updateQuickStats() {
        const bpCount = blueprintUiState.hydrated
            ? document.querySelectorAll('.craft-bp-card:not([data-usage-hidden="true"]), .craft-config-item:not([data-usage-hidden="true"])').length
            : (Array.isArray(getCraftPageConfig().blueprint_configs)
                ? getCraftPageConfig().blueprint_configs.length
                : document.querySelectorAll('.craft-bp-card, .craft-config-item').length);
        const matCount = document.querySelectorAll('#materialsGroupsContainer tbody tr[data-type-id], .craft-item-row').length;
        const blueprintCountEl = document.getElementById('totalBlueprintsCount');
        const materialCountEl = document.getElementById('totalMaterialsCount');
        if (blueprintCountEl) {
            blueprintCountEl.textContent = bpCount || '-';
        }
        if (materialCountEl) {
            materialCountEl.textContent = matCount || '-';
        }
    }

    window.updateCraftQuickStats = updateQuickStats;

    function validateBlueprintRuns() {
        if (!blueprintUiState.hydrated) {
            return;
        }

        const config = getCraftPageConfig();
        const blueprintConfigs = Array.isArray(config.blueprint_configs) ? config.blueprint_configs : [];
        const staticCyclesSummary = config.craft_cycles_summary_static || {};
        const cyclesSummary =
            window.getCraftProductionCyclesSummary && typeof window.getCraftProductionCyclesSummary === 'function'
                ? window.getCraftProductionCyclesSummary() || {}
                : staticCyclesSummary;
        const mainBpInfo = config.main_bp_info || {};
        const mainBpTypeId = (() => {
            const fromInfo = Number(mainBpInfo.type_id || 0);
            if (fromInfo) return fromInfo;
            const fromContext = Number(window.BLUEPRINT_DATA?.bp_type_id || 0);
            if (fromContext) return fromContext;
            const match = window.location.pathname.match(/\/(\d+)\/?$/);
            return match ? Number(match[1]) : 0;
        })();
        const runsInputEl = document.getElementById('runsInput');
        const mainNumRuns = runsInputEl ? Number(runsInputEl.value) || 0 : Number(window.BLUEPRINT_DATA?.num_runs || 0);
        const mainAvailableRuns = mainBpInfo && mainBpInfo.is_copy && mainBpInfo.runs_available != null
            ? Number(mainBpInfo.runs_available) || 0
            : null;

        document.querySelectorAll('.runs-validation-alert').forEach(function (element) {
            element.innerHTML = '';
        });
        document.querySelectorAll('.blueprint-sharing-section[data-bp-owned-copy="1"]').forEach(function (element) {
            element.classList.add('d-none');
        });

        blueprintConfigs.forEach(function (bp) {
            const userOwns = Boolean(bp.user_owns ?? bp.is_owned);
            const isCopyOwned = userOwns && Boolean(bp.is_copy) && bp.runs_available != null;
            const isNotOwned = !userOwns;
            if (!isCopyOwned && !isNotOwned) {
                return;
            }

            const productId = bp.product_type_id || bp.type_id;
            const cyclesData = cyclesSummary[productId] || cyclesSummary[String(productId)];
            let calculatedCycles = cyclesData ? Number(cyclesData.cycles) || 0 : null;
            let availableRuns = isCopyOwned ? Number(bp.runs_available) || 0 : 0;

            if (!cyclesData && Number(bp.type_id) === mainBpTypeId) {
                calculatedCycles = mainNumRuns;
                if (mainAvailableRuns !== null) {
                    availableRuns = mainAvailableRuns;
                }
            }

            if (calculatedCycles === null || calculatedCycles <= availableRuns) {
                return;
            }

            const shortfallRuns = calculatedCycles - availableRuns;
            updateBlueprintCopyRequestRecommendation(bp, shortfallRuns);

            const requestContainer = document.querySelector(`.blueprint-sharing-section[data-bp-owned-copy="1"][data-bp-type-id="${bp.type_id}"]`);
            if (requestContainer) {
                requestContainer.classList.remove('d-none');
            }
        });

        applyBlueprintCardFilters();
        updateQuickStats();

        if (window.CraftBP && typeof window.CraftBP.persistSessionState === 'function') {
            window.CraftBP.persistSessionState();
        }
    }

    function bindCraftStateHandlers() {
        window.validateBlueprintRuns = validateBlueprintRuns;

        document.addEventListener('change', function (event) {
            if (event.target.classList.contains('bp-me-input') || event.target.classList.contains('bp-te-input')) {
                if (window.CraftBP && typeof window.CraftBP.markPendingWorkspaceRefresh === 'function') {
                    window.CraftBP.markPendingWorkspaceRefresh('configure');
                }
            }
        }, true);

        let craftInitPromise = Promise.resolve();
        if (window.CraftBP && typeof window.CraftBP.init === 'function') {
            craftInitPromise = Promise.resolve(window.CraftBP.init({
                productTypeId: String(window.BLUEPRINT_DATA?.product_type_id || 0),
                fuzzworkPriceUrl: pageUrls.fuzzwork_price || window.BLUEPRINT_DATA?.fuzzwork_price_url,
            }));
        }

        document.getElementById('recalcNowBtn')?.addEventListener('click', function () {
            if (window.CraftBP && typeof window.CraftBP.recalculate === 'function') {
                window.CraftBP.recalculate().then(validateBlueprintRuns).catch(validateBlueprintRuns);
            } else {
                window.setTimeout(validateBlueprintRuns, 300);
            }
        });

        return craftInitPromise;
    }

    function bindTabPersistence() {
        const uiVersion = getCraftPageConfig().ui_version || 'v2';
        const bpTypeId = window.BLUEPRINT_DATA?.bp_type_id || 'default';
        const storageKey = `craftBP_${uiVersion}_activeTab_${bpTypeId}`;
        const tabButtons = document.querySelectorAll('#craftMainTabs .nav-link');
        const tabPanes = document.querySelectorAll('#craftMainTabsContent .tab-pane');

        function getCraftTabNavigationType() {
            try {
                if (window.performance && typeof window.performance.getEntriesByType === 'function') {
                    const navigationEntries = window.performance.getEntriesByType('navigation');
                    if (Array.isArray(navigationEntries) && navigationEntries.length > 0) {
                        return navigationEntries[0].type || 'navigate';
                    }
                }
                if (window.performance && window.performance.navigation) {
                    if (window.performance.navigation.type === window.performance.navigation.TYPE_RELOAD) {
                        return 'reload';
                    }
                }
            } catch (error) {
                console.debug('[IndyHub] Unable to inspect tab navigation type', error);
            }
            return 'navigate';
        }

        function showTab(tabBtn) {
            if (!tabBtn) {
                return;
            }
            if (window.bootstrap && window.bootstrap.Tab && typeof window.bootstrap.Tab.getOrCreateInstance === 'function') {
                window.bootstrap.Tab.getOrCreateInstance(tabBtn).show();
                return;
            }
            tabButtons.forEach(function (btn) {
                btn.classList.remove('active');
            });
            tabPanes.forEach(function (pane) {
                pane.classList.remove('show', 'active');
            });
            tabBtn.classList.add('active');
            const targetId = tabBtn.getAttribute('data-bs-target');
            if (targetId) {
                document.querySelector(targetId)?.classList.add('show', 'active');
            }
        }

        function restoreActiveTab() {
            if (getCraftTabNavigationType() !== 'reload') {
                try {
                    window.sessionStorage.removeItem(storageKey);
                } catch (error) {
                    console.debug('[IndyHub] Unable to clear saved main tab', error);
                }
                showTab(tabButtons.length > 0 ? tabButtons[0] : null);
                return;
            }

            const savedTab = window.sessionStorage.getItem(storageKey);
            if (!savedTab) {
                showTab(tabButtons.length > 0 ? tabButtons[0] : null);
                return;
            }

            const tabBtn = document.querySelector(`#craftMainTabs .nav-link[data-tab-name="${savedTab}"]`);
            showTab(tabBtn || (tabButtons.length > 0 ? tabButtons[0] : null));
        }

        tabButtons.forEach(function (button) {
            button.addEventListener('click', function () {
                const tabName = this.getAttribute('data-tab-name');
                if (tabName) {
                    try {
                        window.sessionStorage.setItem(storageKey, tabName);
                    } catch (error) {
                        console.debug('[IndyHub] Unable to save main tab', error);
                    }
                }
            });
        });

        restoreActiveTab();
    }

    function initCraftPage() {
        if (window.CraftBPLoading && typeof window.CraftBPLoading.stepBootstrap === 'function') {
            window.CraftBPLoading.stepBootstrap('loading-interface', {
                detail: __('Preparing navigation and workspace controls.'),
                message: getMessage('loading_workspace', 'Loading workspace...'),
            });
        }

        bindNavigationLoading();
        bindControlForm();
        bindMaterialsGroups();
        bindCsvExports();
        updateQuickStats();
        const craftInitPromise = bindCraftStateHandlers();
        bindTabPersistence();

        window.updateConfigTabFromState = updateConfigTabFromState;

        const configureTabBtn = document.getElementById('configure-tab-btn');
        if (configureTabBtn && configureTabBtn.dataset.lazyBlueprintBound !== 'true') {
            configureTabBtn.addEventListener('shown.bs.tab', updateConfigTabFromState);
            configureTabBtn.dataset.lazyBlueprintBound = 'true';
        }

        if (document.querySelector('#craftMainTabs .nav-link.active')?.getAttribute('data-tab-name') === 'configure') {
            updateConfigTabFromState();
        }

        if (window.CraftBPLoading && typeof window.CraftBPLoading.stepBootstrap === 'function') {
            window.CraftBPLoading.stepBootstrap('loading-prices', {
                detail: __('Fetching prices and updating global calculations.'),
                message: getMessage('loading_workspace', 'Loading workspace...'),
            });
        }

        Promise.resolve(craftInitPromise).finally(function () {
            if (window.CraftBPLoading && typeof window.CraftBPLoading.stepBootstrap === 'function') {
                window.CraftBPLoading.stepBootstrap('finalize', {
                    detail: __('Applying the last updates before the workspace appears.'),
                    message: getMessage('preparing_workspace', 'Preparing production workspace'),
                });
            }

            if (window.CraftBPTabs && typeof window.CraftBPTabs.onAllReady === 'function') {
                window.requestAnimationFrame(function () {
                    window.CraftBPTabs.onAllReady();
                });
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        if (handleDeferredShellHydration()) {
            return;
        }
        initCraftPage();
    });
})();
