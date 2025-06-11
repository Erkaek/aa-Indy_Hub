/**
 * Craft Blueprint JavaScript functionality
 * Handles financial calculations, price fetching, and UI interactions
 */

// Global configuration
const CRAFT_BP = {
    fuzzworkUrl: null, // Will be set from Django template
    productTypeId: null, // Will be set from Django template
};

/**
 * Public API for configuration
 */
window.CraftBP = {
    init: function(config) {
        CRAFT_BP.fuzzworkUrl = config.fuzzworkPriceUrl;
        CRAFT_BP.productTypeId = config.productTypeId;

        // Initialize financial calculations after configuration
        initializeFinancialCalculations();
    }
};

/**
 * Initialize the application
 */
document.addEventListener('DOMContentLoaded', function() {
    initializeBlueprintIcons();
    initializeCollapseHandlers();
    // Financial calculations will be initialized via CraftBP.init()
});

/**
 * Initialize blueprint icon error handling
 */
function initializeBlueprintIcons() {
    document.querySelectorAll('.blueprint-icon img').forEach(function(img) {
        img.onerror = function() {
            this.style.display = 'none';
            if (this.nextElementSibling) {
                this.nextElementSibling.style.display = 'flex';
            }
        };
    });
}

/**
 * Initialize collapse/expand handlers for sub-levels
 */
function initializeCollapseHandlers() {
    document.querySelectorAll('.toggle-subtree').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var targetId = btn.getAttribute('data-target');
            var subtree = document.getElementById(targetId);
            var icon = btn.querySelector('i');
            if (subtree) {
                var expanded = btn.getAttribute('aria-expanded') === 'true';
                subtree.classList.toggle('show', !expanded);
                btn.setAttribute('aria-expanded', !expanded);
                if (!expanded) {
                    icon.classList.remove('fa-chevron-right');
                    icon.classList.add('fa-chevron-down');
                } else {
                    icon.classList.remove('fa-chevron-down');
                    icon.classList.add('fa-chevron-right');
                }
            }
        });
    });
}

/**
 * Initialize financial calculations
 */
function initializeFinancialCalculations() {
    // On change recalc
    const allInputs = Array.from(document.querySelectorAll('.unit-cost, .sale-price-unit'));
    allInputs.forEach(inp => {
        inp.addEventListener('input', recalcFinancials);
    });

    // Batch fetch Fuzzwork prices
    let typeIds = allInputs.map(inp => inp.getAttribute('data-type-id')).filter(Boolean);

    // Include the final product type_id
    if (CRAFT_BP.productTypeId && !typeIds.includes(CRAFT_BP.productTypeId)) {
        typeIds.push(CRAFT_BP.productTypeId);
    }
    typeIds = [...new Set(typeIds)];

    fetchAllPrices(typeIds).then(prices => {
        populatePrices(allInputs, prices);
        recalcFinancials();
    });

    // Initialize purchase list computation
    const computeButton = document.getElementById('compute-needed');
    if (computeButton) {
        computeButton.addEventListener('click', computeNeededPurchases);
    }
}

/**
 * Format a number as a price with ISK suffix
 * @param {number} num - The number to format
 * @returns {string} Formatted price string
 */
function formatPrice(num) {
    return num.toLocaleString('de-DE', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' ISK';
}

/**
 * Format a number with thousand separators
 * @param {number} num - The number to format
 * @returns {string} Formatted number string
 */
function formatNumber(num) {
    return num.toLocaleString('de-DE', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

/**
 * Recalculate financial totals
 */
function recalcFinancials() {
    let costTotal = 0, revTotal = 0;

    document.querySelectorAll('#tab-financial tbody tr').forEach(tr => {
        const qty = parseFloat(tr.children[1].getAttribute('data-qty')) || 0;
        const costInput = tr.querySelector('.unit-cost');
        const revInput = tr.querySelector('.sale-price-unit');

        if (costInput) {
            const cost = (parseFloat(costInput.value) || 0) * qty;
            tr.querySelector('.total-cost').textContent = formatPrice(cost);
            costTotal += cost;
        }

        if (revInput) {
            const rev = (parseFloat(revInput.value) || 0) * qty;
            tr.querySelector('.total-revenue').textContent = formatPrice(rev);
            revTotal += rev;
        }
    });

    document.querySelector('.grand-total-cost').textContent = formatPrice(costTotal);
    document.querySelector('.grand-total-rev').textContent = formatPrice(revTotal);

    const profit = revTotal - costTotal;
    document.querySelector('.profit').childNodes[0].textContent = formatPrice(profit) + ' ';
    const pct = costTotal > 0 ? ((profit / costTotal * 100).toFixed(1)) : '0.0';
    document.querySelector('.profit-pct').textContent = `(${pct}%)`;
}

/**
 * Batch fetch prices from Fuzzwork API
 * @param {Array} typeIds - Array of EVE type IDs
 * @returns {Promise<Object>} Promise resolving to price data
 */
async function fetchAllPrices(typeIds) {
    try {
        const resp = await fetch(`${CRAFT_BP.fuzzworkUrl}?type_id=${typeIds.join(',')}`);
        return await resp.json();
    } catch (e) {
        console.error('Error fetching prices from Fuzzwork', e);
        return {};
    }
}

/**
 * Populate price inputs with fetched data
 * @param {Array} allInputs - Array of input elements
 * @param {Object} prices - Price data from API
 */
function populatePrices(allInputs, prices) {
    // Populate all material and sale price inputs
    allInputs.forEach(inp => {
        const tid = inp.getAttribute('data-type-id');
        const raw = prices[tid];
        let price = raw != null ? parseFloat(raw) : NaN;
        if (isNaN(price)) price = 0;

        inp.value = price.toFixed(2);

        if (price <= 0) {
            inp.classList.add('bg-warning', 'border-warning');
            inp.setAttribute('title', 'Price not available (Fuzzwork)');
        } else {
            inp.classList.remove('bg-warning', 'border-warning');
            inp.removeAttribute('title');
        }
    });

    // Override final product sale price using its true type_id
    if (CRAFT_BP.productTypeId) {
        const rawFinal = prices[CRAFT_BP.productTypeId];
        let finalPrice = rawFinal != null ? parseFloat(rawFinal) : NaN;
        if (isNaN(finalPrice)) finalPrice = 0;

        const saleInput = document.querySelector('.sale-price-unit');
        if (saleInput) {
            saleInput.value = finalPrice.toFixed(2);
            if (finalPrice <= 0) {
                saleInput.classList.add('bg-warning', 'border-warning');
                saleInput.setAttribute('title', 'Price not available (Fuzzwork)');
            } else {
                saleInput.classList.remove('bg-warning', 'border-warning');
                saleInput.removeAttribute('title');
            }
        }
    }
}

/**
 * Compute needed purchase list based on user selections
 */
function computeNeededPurchases() {
    const purchases = {};

    function traverse(summary) {
        const detail = summary.parentElement;
        const childDetails = detail.querySelectorAll(':scope > details');

        if (childDetails.length > 0) {
            // Non-leaf
            const cb = summary.querySelector('.mat-checkbox');
            if (cb && !cb.checked) {
                // User chooses to buy this intermediate product
                const tid = summary.dataset.typeId;
                const name = summary.dataset.typeName;
                const qty = parseInt(summary.dataset.qty) || 0;
                purchases[tid] = purchases[tid] || {name: name, qty: 0};
                purchases[tid].qty += qty;
            } else {
                // Produce: recurse into children
                childDetails.forEach(child => {
                    const childSum = child.querySelector('summary');
                    if (childSum) traverse(childSum);
                });
            }
        } else {
            // Leaf: always purchase raw material
            const tid = summary.dataset.typeId;
            const name = summary.dataset.typeName;
            const qty = parseInt(summary.dataset.qty) || 0;
            purchases[tid] = purchases[tid] || {name: name, qty: 0};
            purchases[tid].qty += qty;
        }
    }

    // Start from roots
    document.querySelectorAll('#tab-tree details > summary').forEach(rootSum => {
        traverse(rootSum);
    });

    // Render purchases
    const tbody = document.querySelector('#needed-table tbody');
    tbody.innerHTML = '';

    // Fetch prices for purchase items
    const pIds = Object.keys(purchases);
    fetchAllPrices(pIds).then(prices => {
        let totalCost = 0;
        Object.entries(purchases).forEach(([tid, item]) => {
            const unit = parseFloat(prices[tid]) || 0;
            const line = unit * item.qty;
            totalCost += line;

            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${item.name}</td>
                <td class="text-end">${formatNumber(item.qty)}</td>
                <td class="text-end">${formatPrice(unit)}</td>
                <td class="text-end">${formatPrice(line)}</td>
            `;
            tbody.appendChild(row);
        });
        document.querySelector('.purchase-total').textContent = formatPrice(totalCost);
    });
}

/**
 * Set configuration values from Django template
 * @param {string} fuzzworkUrl - URL for Fuzzwork API
 * @param {string} productTypeId - Product type ID
 */
function setCraftBPConfig(fuzzworkUrl, productTypeId) {
    CRAFT_BP.fuzzworkUrl = fuzzworkUrl;
    CRAFT_BP.productTypeId = productTypeId;
}
