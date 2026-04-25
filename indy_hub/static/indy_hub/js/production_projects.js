(function () {
    function getRoot() {
        return document.querySelector('.simulations-page[data-project-preview-url]');
    }

    function getCsrfToken() {
        var cookieValue = null;
        var cookies = document.cookie ? document.cookie.split(';') : [];
        cookies.forEach(function (cookie) {
            var trimmed = cookie.trim();
            if (trimmed.startsWith('csrftoken=')) {
                cookieValue = decodeURIComponent(trimmed.substring('csrftoken='.length));
            }
        });
        return cookieValue || '';
    }

    function setStatus(message, variant) {
        var node = document.getElementById('productionProjectImportStatus');
        if (!node) {
            return;
        }
        node.className = 'alert alert-' + (variant || 'secondary');
        node.textContent = message;
    }

    function renderSummary(summary) {
        var container = document.getElementById('productionProjectPreviewSummary');
        if (!container) {
            return;
        }
        if (!summary) {
            container.innerHTML = '';
            return;
        }
        var cards = [
            { label: 'Unique items', value: summary.total_unique_items || 0, icon: 'fa-cubes', tone: 'primary' },
            { label: 'Craftable', value: summary.craftable_items || 0, icon: 'fa-industry', tone: 'success' },
            { label: 'Not craftable', value: summary.non_craftable_items || 0, icon: 'fa-cart-shopping', tone: 'warning' },
            { label: 'Total quantity', value: summary.total_quantity || 0, icon: 'fa-sort-amount-up', tone: 'info' }
        ];
        container.innerHTML = cards.map(function (card) {
            return '' +
                '<div class="col-sm-6 col-xl-3">' +
                '  <div class="summary-card h-100">' +
                '    <div class="summary-card__icon bg-' + card.tone + '-subtle text-' + card.tone + '">' +
                '      <i class="fas ' + card.icon + '"></i>' +
                '    </div>' +
                '    <div>' +
                '      <p class="summary-card__label mb-1">' + escapeHtml(card.label) + '</p>' +
                '      <h5 class="summary-card__value mb-0">' + formatInteger(card.value) + '</h5>' +
                '    </div>' +
                '  </div>' +
                '</div>';
        }).join('');
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatInteger(value) {
        var numericValue = Number(value || 0);
        if (!Number.isFinite(numericValue)) {
            return '0';
        }
        return Math.max(0, Math.round(numericValue)).toLocaleString();
    }

    function buildItemRow(item, index) {
        var checkboxId = 'projectImportItem_' + index;
        var resolved = item.resolved !== false;
        var craftable = !!item.is_craftable;
        var disabled = !resolved;
        var checked = resolved;
        var stateBadge = craftable
            ? '<span class="badge bg-success-subtle text-success">Craftable</span>'
            : '<span class="badge bg-warning-subtle text-warning">Buy only</span>';
        if (!resolved) {
            stateBadge = '<span class="badge bg-danger-subtle text-danger">Unknown item</span>';
        }

        return '' +
            '<tr data-item-index="' + index + '">' +
            '  <td>' +
            '    <div class="form-check mb-0">' +
            '      <input class="form-check-input production-project-item-toggle" type="checkbox" id="' + checkboxId + '" ' + (checked ? 'checked ' : '') + (disabled ? 'disabled ' : '') + 'data-item-index="' + index + '">' +
            '      <label class="form-check-label" for="' + checkboxId + '">' +
            '        <span class="fw-semibold">' + escapeHtml(item.type_name || '') + '</span>' +
            '      </label>' +
            '    </div>' +
            '  </td>' +
            '  <td class="text-end"><span class="badge bg-primary text-white">' + formatInteger(item.quantity || 0) + '</span></td>' +
            '  <td>' + stateBadge + '</td>' +
            '  <td class="text-muted small">' + escapeHtml(item.group_name || '') + '</td>' +
            '</tr>';
    }

    function renderGroups(groups, entries) {
        var container = document.getElementById('productionProjectPreviewGroups');
        if (!container) {
            return;
        }
        if (!groups || !groups.length) {
            container.innerHTML = '<div class="alert alert-light border mb-0">No items resolved.</div>';
            return;
        }

        var entryBySignature = new Map();
        (entries || []).forEach(function (entry, index) {
            var key = String(entry.type_id || '') + '::' + String(entry.type_name || '').toLowerCase();
            if (!entryBySignature.has(key)) {
                entryBySignature.set(key, []);
            }
            entryBySignature.get(key).push({ item: entry, index: index });
        });

        container.innerHTML = groups.map(function (group) {
            var rows = (group.items || []).map(function (item) {
                var key = String(item.type_id || '') + '::' + String(item.type_name || '').toLowerCase();
                var matches = entryBySignature.get(key) || [];
                var match = matches.shift();
                return buildItemRow(match ? match.item : item, match ? match.index : 0);
            }).join('');

            return '' +
                '<section class="card shadow-sm">' +
                '  <div class="card-header d-flex align-items-center justify-content-between bg-body-secondary">' +
                '    <span class="fw-semibold">' + escapeHtml(group.label || 'Group') + '</span>' +
                '    <span class="small text-muted">' + formatInteger((group.items || []).length) + '</span>' +
                '  </div>' +
                '  <div class="card-body p-0">' +
                '    <div class="table-responsive">' +
                '      <table class="table table-sm align-middle mb-0">' +
                '        <thead class="table-light">' +
                '          <tr><th>Item</th><th class="text-end">Qty</th><th>Status</th><th>Group</th></tr>' +
                '        </thead>' +
                '        <tbody>' + rows + '</tbody>' +
                '      </table>' +
                '    </div>' +
                '  </div>' +
                '</section>';
        }).join('');
    }

    function collectSelectedItems(preview) {
        var includeBuy = !!document.getElementById('productionProjectIncludeBuy')?.checked;
        return (preview.entries || []).map(function (item, index) {
            var checkbox = document.querySelector('.production-project-item-toggle[data-item-index="' + index + '"]');
            var selected = checkbox ? checkbox.checked : false;
            var inclusionMode = item.is_craftable ? 'produce' : (includeBuy ? 'buy' : 'skip');
            return Object.assign({}, item, {
                is_selected: selected,
                inclusion_mode: selected ? inclusionMode : 'skip'
            });
        }).filter(function (item) {
            return item.is_selected && item.inclusion_mode !== 'skip';
        });
    }

    async function requestJson(url, payload) {
        var response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(payload || {})
        });
        var data = await response.json();
        if (!response.ok || !data || data.success === false) {
            throw new Error((data && data.error) || 'Request failed');
        }
        return data;
    }

    function readJsonScript(id) {
        var node = document.getElementById(id);
        if (!node) {
            return null;
        }
        try {
            return JSON.parse(node.textContent || 'null');
        } catch (error) {
            console.error('[ProductionProjects] Invalid JSON script payload', id, error);
            return null;
        }
    }

    function normalizeProgressState(rawState) {
        var items = rawState && Array.isArray(rawState.items) ? rawState.items : [];
        var validIds = new Set(items.map(function (item) {
            return String(item.id || '');
        }));
        var completedIds = new Set((rawState && Array.isArray(rawState.completed_ids) ? rawState.completed_ids : []).map(function (activityId) {
            return String(activityId || '');
        }).filter(function (activityId) {
            return validIds.has(activityId);
        }));
        var inProgressIds = new Set((rawState && Array.isArray(rawState.in_progress_ids) ? rawState.in_progress_ids : []).map(function (activityId) {
            return String(activityId || '');
        }).filter(function (activityId) {
            return validIds.has(activityId) && !completedIds.has(activityId);
        }));
        var rawLinkedJobIdsByItem = rawState && rawState.linked_job_ids_by_item && typeof rawState.linked_job_ids_by_item === 'object'
            ? rawState.linked_job_ids_by_item
            : {};

        var linkedJobIdsByItem = {};

        function normalizeJob(job) {
            return Object.assign({}, job || {}, {
                id: String((job && (job.id || job.job_id)) || ''),
                job_id: String((job && job.job_id) || ''),
                total_output_quantity: Math.max(0, Number(job && job.total_output_quantity) || 0),
                completed_output_quantity: Math.max(0, Number(job && job.completed_output_quantity) || 0),
                progress_output_quantity: Math.max(0, Number(job && job.progress_output_quantity) || 0),
                progress_percent: Math.max(0, Math.min(100, Number(job && job.progress_percent) || 0)),
                is_active: !!(job && job.is_active),
                is_completed: !!(job && job.is_completed)
            });
        }

        var normalizedItems = items.map(function (item) {
            var itemId = String(item.id || '');
            var quantityRequested = Math.max(0, Number(item.quantity_requested) || 0);
            var availableJobs = Array.isArray(item.available_jobs) ? item.available_jobs.map(normalizeJob) : [];
            var validJobIds = new Set(availableJobs.map(function (job) {
                return String(job.job_id || '');
            }).filter(Boolean));
            var linkedJobIds = Array.isArray(rawLinkedJobIdsByItem[itemId])
                ? rawLinkedJobIdsByItem[itemId].map(function (jobId) { return String(jobId || ''); }).filter(function (jobId) { return validJobIds.has(jobId); })
                : (Array.isArray(item.linked_job_ids) ? item.linked_job_ids.map(function (jobId) { return String(jobId || ''); }).filter(function (jobId) { return validJobIds.has(jobId); }) : []);
            if (linkedJobIds.length) {
                linkedJobIdsByItem[itemId] = Array.from(new Set(linkedJobIds)).sort();
            }

            var linkedJobIdSet = new Set(linkedJobIdsByItem[itemId] || []);
            var normalizedJobs = availableJobs.map(function (job) {
                return Object.assign({}, job, {
                    is_linked: linkedJobIdSet.has(String(job.job_id || ''))
                });
            });
            var linkedJobs = normalizedJobs.filter(function (job) {
                return job.is_linked;
            });
            var autoCompletedQuantity = Math.min(quantityRequested, linkedJobs.reduce(function (sum, job) {
                return sum + Math.max(0, Number(job.completed_output_quantity) || 0);
            }, 0));
            var autoProgressQuantity = Math.min(quantityRequested, Math.max(autoCompletedQuantity, linkedJobs.reduce(function (sum, job) {
                return sum + Math.max(0, Number(job.progress_output_quantity) || 0);
            }, 0)));
            var hasLinkedJobs = linkedJobs.length > 0;
            var manualIsCompleted = completedIds.has(itemId) || !!item.manual_is_completed;
            var manualIsInProgress = (inProgressIds.has(itemId) || !!item.manual_is_in_progress) && !manualIsCompleted;
            var isCompleted = hasLinkedJobs ? (quantityRequested === 0 ? true : autoCompletedQuantity >= quantityRequested) : manualIsCompleted;
            var isInProgress = hasLinkedJobs
                ? (!isCompleted && linkedJobs.some(function (job) { return !!job.is_active; }))
                : manualIsInProgress;
            var completedQuantity = hasLinkedJobs ? autoCompletedQuantity : (manualIsCompleted ? quantityRequested : 0);
            var progressQuantity = hasLinkedJobs ? autoProgressQuantity : completedQuantity;

            return Object.assign({}, item, {
                quantity_requested: quantityRequested,
                available_jobs: normalizedJobs,
                linked_jobs: linkedJobs,
                linked_job_ids: linkedJobIdsByItem[itemId] || [],
                linked_job_count: linkedJobs.length,
                auto_completed_quantity: autoCompletedQuantity,
                auto_progress_quantity: autoProgressQuantity,
                manual_is_completed: manualIsCompleted,
                manual_is_in_progress: manualIsInProgress,
                completed_quantity: completedQuantity,
                progress_quantity: progressQuantity,
                is_in_progress: isInProgress,
                is_completed: isCompleted
            });
        });
        var totalCount = normalizedItems.length;
        var totalQuantity = normalizedItems.reduce(function (sum, item) {
            return sum + Math.max(0, Number(item.quantity_requested) || 0);
        }, 0);
        var completedCount = normalizedItems.filter(function (item) {
            return item.is_completed;
        }).length;
        var inProgressCount = normalizedItems.filter(function (item) {
            return item.is_in_progress;
        }).length;
        var completedQuantity = normalizedItems.reduce(function (sum, item) {
            return sum + Math.max(0, Number(item.completed_quantity) || 0);
        }, 0);
        var progressQuantity = normalizedItems.reduce(function (sum, item) {
            return sum + Math.max(0, Number(item.progress_quantity) || 0);
        }, 0);
        var completionPercentage = totalQuantity ? Math.round((progressQuantity / totalQuantity) * 100) : 0;

        return {
            items: normalizedItems,
            in_progress_ids: Array.from(inProgressIds),
            completed_ids: Array.from(completedIds),
            linked_job_ids_by_item: linkedJobIdsByItem,
            total_count: totalCount,
            total_quantity: totalQuantity,
            completed_count: completedCount,
            completed_quantity: completedQuantity,
            progress_quantity: progressQuantity,
            in_progress_count: inProgressCount,
            completion_percentage: completionPercentage
        };
    }

    function setProgressStatus(message, variant) {
        var node = document.getElementById('productionProjectProgressStatus');
        if (!node) {
            return;
        }
        node.className = 'alert alert-' + (variant || 'secondary') + ' mt-4 mb-0';
        node.textContent = message;
    }

    function renderProgressSummary(progress) {
        var summaryText = document.getElementById('productionProjectProgressSummaryText');
        var summaryMeta = document.getElementById('productionProjectProgressSummaryMeta');
        var summaryStats = document.getElementById('productionProjectProgressSummaryStats');
        var bar = document.getElementById('productionProjectProgressModalBar');
        var percentage = Number(progress && progress.completion_percentage) || 0;
        var completedQuantity = Number(progress && progress.completed_quantity) || 0;
        var progressQuantity = Number(progress && progress.progress_quantity) || 0;
        var inProgressCount = Number(progress && progress.in_progress_count) || 0;
        var totalCount = Number(progress && progress.total_count) || 0;
        var totalQuantity = Number(progress && progress.total_quantity) || 0;
        var autoLinkedCount = Array.isArray(progress && progress.items)
            ? progress.items.filter(function (item) {
                return Array.isArray(item.linked_job_ids) && item.linked_job_ids.length > 0;
            }).length
            : 0;

        if (summaryText) {
            summaryText.textContent = percentage + '%';
        }
        if (summaryMeta) {
            if (totalCount > 0) {
                summaryMeta.innerHTML = '' +
                    '<div class="project-progress-summary-copy__headline">Coverage across active outputs</div>' +
                    '<div class="project-progress-summary-copy__text">' +
                    escapeHtml(formatInteger(progressQuantity) + ' / ' + formatInteger(totalQuantity) + ' units covered across ' + formatInteger(totalCount) + ' production lines.') +
                    '</div>';
            } else {
                summaryMeta.innerHTML = '' +
                    '<div class="project-progress-summary-copy__headline">Nothing to track yet</div>' +
                    '<div class="project-progress-summary-copy__text">No production lines are currently tracked on this project.</div>';
            }
        }
        if (summaryStats) {
            if (totalCount > 0) {
                summaryStats.innerHTML = [
                    {
                        label: 'Delivered',
                        value: formatInteger(completedQuantity) + ' units',
                        meta: 'Outputs fully covered',
                        icon: 'fa-box-open',
                        tone: 'success'
                    },
                    {
                        label: 'In production',
                        value: formatInteger(inProgressCount) + ' lines',
                        meta: 'Lines still moving',
                        icon: 'fa-gears',
                        tone: 'info'
                    },
                    {
                        label: 'Auto-linked',
                        value: formatInteger(autoLinkedCount) + ' items',
                        meta: 'Driven by synced jobs',
                        icon: 'fa-link',
                        tone: 'primary'
                    }
                ].map(function (stat) {
                    return '' +
                        '<div class="project-progress-stat-chip tone-' + stat.tone + '">' +
                        '  <span class="project-progress-stat-chip__icon"><i class="fas ' + stat.icon + '"></i></span>' +
                        '  <span class="project-progress-stat-chip__label">' + escapeHtml(stat.label) + '</span>' +
                        '  <span class="project-progress-stat-chip__value">' + escapeHtml(stat.value) + '</span>' +
                        '  <span class="project-progress-stat-chip__meta">' + escapeHtml(stat.meta) + '</span>' +
                        '</div>';
                }).join('');
            } else {
                summaryStats.innerHTML = '';
            }
        }
        if (bar) {
            bar.style.width = percentage + '%';
            bar.textContent = percentage + '%';
            bar.classList.toggle('progress-bar-striped', inProgressCount > 0);
            bar.classList.toggle('progress-bar-animated', inProgressCount > 0);
            if (bar.parentElement) {
                bar.parentElement.setAttribute('aria-valuenow', String(percentage));
            }
        }
    }

    function renderProgressRows(progress) {
        var container = document.getElementById('productionProjectProgressRows');
        if (!container) {
            return;
        }

        if (!progress.items || !progress.items.length) {
            container.innerHTML = '<div class="alert alert-light border mb-0">No production items are currently tracked on this project.</div>';
            return;
        }

        container.innerHTML = (progress.items || []).map(function (item, index) {
            var itemId = escapeHtml(item.id || '');
            var inProgressId = 'projectProgressInProgress_' + index;
            var completedId = 'projectProgressCompleted_' + index;
            var hasLinkedJobs = Array.isArray(item.linked_job_ids) && item.linked_job_ids.length > 0;
            var quantityRequested = Math.max(0, Number(item.quantity_requested) || 0);
            var progressQuantity = Math.max(0, Number(item.progress_quantity) || 0);
            var completedQuantity = Math.max(0, Number(item.completed_quantity) || 0);
            var linkedJobCount = Math.max(0, Number(item.linked_job_count) || 0);
            var activeJobCount = Array.isArray(item.linked_jobs)
                ? item.linked_jobs.filter(function (job) { return !!job.is_active; }).length
                : 0;
            var rowPercentage = quantityRequested ? Math.max(0, Math.min(100, Math.round((progressQuantity / quantityRequested) * 100))) : 0;
            var itemName = escapeHtml(item.type_name || 'Output item');
            var iconMarkup = Number(item.type_id || 0) > 0
                ? '<img src="https://images.evetech.net/types/' + Number(item.type_id) + '/icon?size=64" alt="' + itemName + '" loading="lazy" decoding="async" class="project-progress-row__icon-image" onerror="this.style.display=\'none\'; this.nextElementSibling.classList.remove(\'d-none\');">' +
                    '<span class="project-progress-row__icon-fallback d-none"><i class="fas fa-boxes-stacked"></i></span>'
                : '<span class="project-progress-row__icon-fallback"><i class="fas fa-boxes-stacked"></i></span>';
            var modeBadge = hasLinkedJobs
                ? '<span class="project-progress-pill tone-primary"><i class="fas fa-link"></i>Auto from jobs</span>'
                : '<span class="project-progress-pill tone-muted"><i class="fas fa-hand"></i>Manual tracking</span>';
            var stageBadge = rowPercentage >= 100
                ? '<span class="project-progress-pill tone-success"><i class="fas fa-circle-check"></i>Ready</span>'
                : (rowPercentage > 0
                    ? '<span class="project-progress-pill tone-info"><i class="fas fa-hourglass-half"></i>Partial</span>'
                    : '<span class="project-progress-pill tone-slate"><i class="fas fa-clock"></i>Waiting</span>');
            var autoSummary = hasLinkedJobs
                ? formatInteger(progressQuantity) + ' / ' + formatInteger(quantityRequested) + ' units covered by linked jobs'
                : 'No linked jobs yet';
            var jobsMarkup = '';
            if (Array.isArray(item.available_jobs) && item.available_jobs.length) {
                jobsMarkup = '<div class="project-progress-job-list">' + item.available_jobs.map(function (job, jobIndex) {
                    var checkboxId = 'projectProgressJobLink_' + index + '_' + jobIndex;
                    var progressLabel = job.is_completed
                        ? 'Delivered'
                        : (job.is_active ? (formatInteger(job.progress_output_quantity || 0) + ' / ' + formatInteger(job.total_output_quantity || 0) + ' units') : 'Tracked');
                    var ownerBits = [job.character_name || '', job.location_name || ''].filter(Boolean).join(' • ');
                    var statusTone = job.is_completed ? 'success' : (job.is_active ? 'info' : 'muted');
                    return '' +
                        '<label class="project-progress-job' + (job.is_linked ? ' is-linked' : '') + '" for="' + checkboxId + '">' +
                        '  <div class="project-progress-job__check form-check mb-0">' +
                        '    <input class="form-check-input project-job-link-toggle" type="checkbox" id="' + checkboxId + '" data-item-id="' + itemId + '" data-job-id="' + escapeHtml(job.job_id || '') + '" ' + (job.is_linked ? 'checked' : '') + '>' +
                        '  </div>' +
                        '  <div class="project-progress-job__body">' +
                        '    <div class="project-progress-job__header">' +
                        '      <div class="project-progress-job__title">Job #' + escapeHtml(job.job_id || '') + '</div>' +
                        '      <span class="project-progress-pill tone-' + statusTone + '">' + escapeHtml(job.status_label || job.status || 'Tracked') + '</span>' +
                        '    </div>' +
                        '    <div class="project-progress-job__meta">' + escapeHtml(ownerBits || 'Unknown owner / location') + '</div>' +
                        '    <div class="project-progress-job__facts">' +
                        '      <span class="project-progress-job__fact"><strong>' + formatInteger(job.total_output_quantity || 0) + '</strong><em>Total output</em></span>' +
                        '      <span class="project-progress-job__fact"><strong>' + escapeHtml(progressLabel) + '</strong><em>Coverage</em></span>' +
                        '    </div>' +
                        '  </div>' +
                        '</label>';
                }).join('') + '</div>';
            } else {
                jobsMarkup = '<div class="project-progress-job-list"><div class="project-progress-empty-state">No tracked jobs currently match this output item.</div></div>';
            }
            return '' +
                '<div class="project-progress-row' + (hasLinkedJobs ? ' is-auto' : '') + '" data-progress-activity-id="' + itemId + '">' +
                '  <div class="project-progress-row__shell">' +
                '    <aside class="project-progress-row__aside">' +
                '      <div class="project-progress-row__item-head">' +
                '        <div class="project-progress-row__icon">' + iconMarkup + '</div>' +
                '        <div class="project-progress-row__identity">' +
                '          <div class="project-progress-row__eyebrow">Output item</div>' +
                '          <div class="project-progress-row__title">' + itemName + '</div>' +
                '          <div class="project-progress-row__description">' + escapeHtml(autoSummary) + '</div>' +
                '        </div>' +
                '      </div>' +
                '      <div class="project-progress-row__stat-grid">' +
                '        <div class="project-progress-mini-stat"><span>Target</span><strong>' + formatInteger(quantityRequested) + '</strong></div>' +
                '        <div class="project-progress-mini-stat"><span>Covered</span><strong>' + formatInteger(progressQuantity) + '</strong></div>' +
                '        <div class="project-progress-mini-stat"><span>Delivered</span><strong>' + formatInteger(completedQuantity) + '</strong></div>' +
                '        <div class="project-progress-mini-stat"><span>Linked jobs</span><strong>' + formatInteger(linkedJobCount) + '</strong></div>' +
                '      </div>' +
                '      <div class="project-progress-row__meter-wrap">' +
                '        <div class="progress project-progress-row__meter" role="progressbar" aria-valuenow="' + rowPercentage + '" aria-valuemin="0" aria-valuemax="100">' +
                '          <div class="progress-bar" style="width: ' + rowPercentage + '%;">' + rowPercentage + '%</div>' +
                '        </div>' +
                '        <div class="project-progress-row__meter-caption">' + formatInteger(progressQuantity) + ' / ' + formatInteger(quantityRequested) + ' units covered</div>' +
                '      </div>' +
                '      <div class="project-progress-row__badges">' +
                stageBadge +
                '        <span class="project-progress-pill tone-slate"><i class="fas fa-wave-square"></i>' + formatInteger(activeJobCount) + ' active</span>' +
                modeBadge +
                '      </div>' +
                '    </aside>' +
                '    <div class="project-progress-row__body">' +
                '      <div class="project-progress-panel project-progress-panel--jobs">' +
                '        <div class="project-progress-panel__heading">Matching jobs</div>' +
                '        <div class="project-progress-panel__subheading">Link tracked jobs to drive this output automatically from real delivered or active quantities.</div>' +
                jobsMarkup +
                '      </div>' +
                '      <div class="project-progress-panel project-progress-panel--status">' +
                '        <div class="project-progress-panel__heading">Manual status</div>' +
                '        <div class="project-progress-panel__subheading">' + (hasLinkedJobs ? 'This line is currently controlled by linked jobs. Unlink them if you need manual overrides.' : 'Use manual states until a tracked job exists for this output.') + '</div>' +
                '        <div class="project-progress-row__state-list">' +
                '          <div class="project-progress-row__state">' +
                '            <div class="project-progress-row__state-copy">' +
                '              <strong>In production</strong>' +
                '              <span>Mark the line as actively being built.</span>' +
                '            </div>' +
                '            <div class="form-check mb-0">' +
                '              <input class="form-check-input project-progress-toggle" type="checkbox" id="' + inProgressId + '" data-progress-state="in_progress" data-activity-id="' + itemId + '" ' + (item.manual_is_in_progress ? 'checked' : '') + ' ' + (hasLinkedJobs ? 'disabled' : '') + '>' +
                '              <label class="form-check-label" for="' + inProgressId + '">Active</label>' +
                '            </div>' +
                '          </div>' +
                '          <div class="project-progress-row__state">' +
                '            <div class="project-progress-row__state-copy">' +
                '              <strong>Produced</strong>' +
                '              <span>Use this when the requested quantity has been finished.</span>' +
                '            </div>' +
                '            <div class="form-check mb-0">' +
                '              <input class="form-check-input project-progress-toggle" type="checkbox" id="' + completedId + '" data-progress-state="completed" data-activity-id="' + itemId + '" ' + (item.manual_is_completed ? 'checked' : '') + ' ' + (hasLinkedJobs ? 'disabled' : '') + '>' +
                '              <label class="form-check-label" for="' + completedId + '">Done</label>' +
                '            </div>' +
                '          </div>' +
                '        </div>' +
                '      </div>' +
                '    </div>' +
                '    </div>' +
                '</div>';
        }).join('');
    }

    function collectProgressFromModal(baseProgress) {
        var inProgressIds = [];
        var completedIds = [];
        var linkedJobIdsByItem = {};
        document.querySelectorAll('#productionProjectProgressRows .project-progress-toggle').forEach(function (input) {
            if (!input.checked) {
                return;
            }
            var activityId = String(input.dataset.activityId || '');
            if (!activityId) {
                return;
            }
            if (input.dataset.progressState === 'completed') {
                completedIds.push(activityId);
                return;
            }
            inProgressIds.push(activityId);
        });
        document.querySelectorAll('#productionProjectProgressRows .project-job-link-toggle').forEach(function (input) {
            if (!input.checked) {
                return;
            }
            var itemId = String(input.dataset.itemId || '');
            var jobId = String(input.dataset.jobId || '');
            if (!itemId || !jobId) {
                return;
            }
            if (!linkedJobIdsByItem[itemId]) {
                linkedJobIdsByItem[itemId] = [];
            }
            linkedJobIdsByItem[itemId].push(jobId);
        });
        return normalizeProgressState({
            items: baseProgress && Array.isArray(baseProgress.items) ? baseProgress.items : [],
            in_progress_ids: inProgressIds,
            completed_ids: completedIds,
            linked_job_ids_by_item: linkedJobIdsByItem
        });
    }

    function updateProgressCard(card, progress) {
        if (!card) {
            return;
        }

        var normalized = normalizeProgressState(progress);
        var bar = card.querySelector('[data-project-progress-bar]');
        var summary = card.querySelector('[data-project-progress-summary]');
        var label = card.querySelector('[data-project-progress-label]');
        var percentage = normalized.completion_percentage;

        if (bar) {
            bar.style.width = percentage + '%';
            bar.classList.toggle('progress-bar-striped', normalized.in_progress_count > 0);
            bar.classList.toggle('progress-bar-animated', normalized.in_progress_count > 0);
            if (bar.parentElement) {
                bar.parentElement.setAttribute('aria-valuenow', String(percentage));
            }
        }
        if (label) {
            label.textContent = percentage + '%';
        }
        if (summary) {
            if (normalized.total_count > 0) {
                summary.textContent = normalized.progress_quantity + ' / ' + normalized.total_quantity + ' units covered • ' + normalized.in_progress_count + ' lines in production';
            } else {
                summary.textContent = 'No production lines';
            }
        }
    }

    function bindEvents(root) {
        var previewButton = document.getElementById('productionProjectPreviewBtn');
        var createButton = document.getElementById('productionProjectCreateBtn');
        var sourceTextInput = document.getElementById('productionProjectSourceText');
        var sourceKindInput = document.getElementById('productionProjectSourceKind');
        var nameInput = document.getElementById('productionProjectName');
        var statusInput = document.getElementById('productionProjectStatus');
        var modal = document.getElementById('productionProjectImportModal');
        var progressModal = document.getElementById('productionProjectProgressModal');
        var progressSaveButton = document.getElementById('productionProjectProgressSaveBtn');
        var state = { preview: null };
        var progressState = {
            saveUrl: null,
            triggerButton: null,
            card: null,
            progress: null
        };

        if (!previewButton || !createButton || !sourceTextInput || !sourceKindInput || !nameInput || !statusInput) {
            return;
        }

        previewButton.addEventListener('click', async function () {
            var sourceText = sourceTextInput.value.trim();
            if (!sourceText) {
                setStatus('Paste an EFT fitting or enter a manual item list first.', 'warning');
                return;
            }

            previewButton.disabled = true;
            createButton.disabled = true;
            setStatus('Resolving items and checking craftability…', 'info');

            try {
                var preview = await requestJson(root.dataset.projectPreviewUrl, {
                    source_text: sourceText,
                    source_kind: sourceKindInput.value
                });
                state.preview = preview;
                renderSummary(preview.summary || {});
                renderGroups(preview.groups || [], preview.entries || []);
                if (!nameInput.value.trim() && preview.source_name) {
                    nameInput.value = preview.source_name;
                }
                if (preview.source_kind) {
                    sourceKindInput.value = preview.source_kind;
                }
                setStatus('Review the imported items, uncheck what should stay out of the project, then create the table.', 'success');
                createButton.disabled = false;
            } catch (error) {
                console.error('[ProductionProjects] Preview failed', error);
                renderSummary(null);
                renderGroups([], []);
                setStatus(error.message || 'Unable to preview the import.', 'danger');
            } finally {
                previewButton.disabled = false;
            }
        });

        createButton.addEventListener('click', async function () {
            if (!state.preview) {
                setStatus('Preview the import before creating the project.', 'warning');
                return;
            }

            var selectedItems = collectSelectedItems(state.preview);
            if (!selectedItems.length) {
                setStatus('Select at least one resolved item to create the project.', 'warning');
                return;
            }

            createButton.disabled = true;
            setStatus('Creating craft table…', 'info');

            try {
                var result = await requestJson(root.dataset.projectCreateUrl, {
                    name: nameInput.value.trim(),
                    status: statusInput.value,
                    source_text: sourceTextInput.value,
                    source_kind: state.preview.source_kind || sourceKindInput.value,
                    source_name: state.preview.source_name || '',
                    include_non_craftable_as_buy: !!document.getElementById('productionProjectIncludeBuy')?.checked,
                    items: selectedItems
                });
                if (modal && window.bootstrap && window.bootstrap.Modal) {
                    var instance = window.bootstrap.Modal.getInstance(modal) || window.bootstrap.Modal.getOrCreateInstance(modal);
                    if (instance) {
                        instance.hide();
                    }
                }
                window.location.href = result.redirect_url;
            } catch (error) {
                console.error('[ProductionProjects] Creation failed', error);
                setStatus(error.message || 'Unable to create the craft table.', 'danger');
                createButton.disabled = false;
            }
        });

        if (modal) {
            modal.addEventListener('hidden.bs.modal', function () {
                state.preview = null;
                renderSummary(null);
                renderGroups([], []);
                createButton.disabled = true;
                setStatus('Preview the import to review craftable and non-craftable lines.', 'secondary');
            });
        }

        root.addEventListener('click', function (event) {
            var progressButton = event.target.closest('[data-project-progress-button]');
            if (!progressButton || !progressModal) {
                return;
            }

            progressState.saveUrl = progressButton.dataset.projectProgressSaveUrl || '';
            progressState.triggerButton = progressButton;
            progressState.card = progressButton.closest('[data-project-progress-card]');

            var projectName = progressButton.dataset.projectName || 'Project';
            var description = document.getElementById('productionProjectProgressModalDescription');
            var title = document.getElementById('productionProjectProgressModalLabel');
            var eyebrow = document.getElementById('productionProjectProgressModalEyebrow');
            if (description) {
                description.textContent = 'Link tracked jobs to the items to build for ' + projectName + ', or keep manual tracking when no job matches yet.';
            }
            if (title) {
                title.textContent = projectName;
            }
            if (eyebrow) {
                eyebrow.textContent = 'Production tracking';
            }

            var progress = normalizeProgressState(readJsonScript(progressButton.dataset.projectProgressJsonId || ''));
            progressState.progress = progress;
            renderProgressRows(progress);
            renderProgressSummary(progress);
            setProgressStatus('Link tracked jobs when available. Manual statuses remain for lines without linked jobs.', 'secondary');

            if (window.bootstrap && window.bootstrap.Modal) {
                window.bootstrap.Modal.getOrCreateInstance(progressModal).show();
            }
        });

        if (progressModal) {
            progressModal.addEventListener('change', function (event) {
                var input = event.target;
                if (!input) {
                    return;
                }

                if (input.classList.contains('project-progress-toggle')) {
                    if (input.checked) {
                        progressModal.querySelectorAll('.project-progress-toggle[data-activity-id="' + CSS.escape(input.dataset.activityId || '') + '"]').forEach(function (candidate) {
                            if (candidate !== input) {
                                candidate.checked = false;
                            }
                        });
                    }

                    progressState.progress = collectProgressFromModal(progressState.progress);
                    renderProgressSummary(progressState.progress);
                    return;
                }

                if (input.classList.contains('project-job-link-toggle')) {
                    progressState.progress = collectProgressFromModal(progressState.progress);
                    renderProgressRows(progressState.progress);
                    renderProgressSummary(progressState.progress);
                }
            });

            progressModal.addEventListener('hidden.bs.modal', function () {
                progressState.saveUrl = null;
                progressState.triggerButton = null;
                progressState.card = null;
                progressState.progress = null;
            });
        }

        if (progressSaveButton) {
            progressSaveButton.addEventListener('click', async function () {
                if (!progressState.saveUrl) {
                    setProgressStatus('Progress saving is not configured for this project.', 'warning');
                    return;
                }

                var payload = collectProgressFromModal(progressState.progress);
                progressSaveButton.disabled = true;
                setProgressStatus('Saving production tracking…', 'info');

                try {
                    var result = await requestJson(progressState.saveUrl, {
                        in_progress_ids: payload.in_progress_ids,
                        completed_ids: payload.completed_ids,
                        linked_job_ids_by_item: payload.linked_job_ids_by_item
                    });
                    var normalized = normalizeProgressState(result.progress || payload);
                    progressState.progress = normalized;
                    renderProgressRows(normalized);
                    renderProgressSummary(normalized);
                    updateProgressCard(progressState.card, normalized);

                    var jsonId = progressState.triggerButton && progressState.triggerButton.dataset.projectProgressJsonId;
                    if (jsonId) {
                        var scriptNode = document.getElementById(jsonId);
                        if (scriptNode) {
                            scriptNode.textContent = JSON.stringify(normalized);
                        }
                    }

                    setProgressStatus('Production tracking saved.', 'success');
                } catch (error) {
                    console.error('[ProductionProjects] Progress save failed', error);
                    setProgressStatus(error.message || 'Unable to save project progress.', 'danger');
                } finally {
                    progressSaveButton.disabled = false;
                }
            });
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        var root = getRoot();
        if (!root) {
            return;
        }
        bindEvents(root);
    });
})();
