const fs = require('fs');
const path = require('path');
const vm = require('vm');

const filePath = path.join(__dirname, '..', 'indy_hub', 'static', 'indy_hub', 'js', 'craft_bp_financial_planner.js');
const source = fs.readFileSync(filePath, 'utf8');

function extractFunction(sourceText, functionName, nextFunctionName = null) {
    const lines = sourceText.split(/\n/);
    const startLine = lines.findIndex((line) => line.includes(`function ${functionName}`));
    if (startLine === -1) {
        throw new Error(`Function ${functionName} not found`);
    }

    let endLine = lines.length;
    if (nextFunctionName) {
        const nextLine = lines.findIndex((line, index) => index > startLine && line.includes(`function ${nextFunctionName}`));
        if (nextLine !== -1) {
            endLine = nextLine;
        }
    }

    return lines.slice(startLine, endLine).join('\n');
}

function makeInput() {
    return {
        value: '0.00',
        dataset: {},
        classList: { add() {}, remove() {}, toggle() {} },
        setAttribute() {},
        removeAttribute() {},
    };
}

function makeRow(typeId, isFinalOutput = false) {
    const fuzzInput = makeInput();
    const saleInput = makeInput();
    const realInput = makeInput();
    return {
        _typeId: typeId,
        _isFinalOutput: isFinalOutput,
        _fuzzInput: fuzzInput,
        _saleInput: saleInput,
        _realInput: realInput,
        parentElement: null,
        hidden: false,
        dataset: {},
        getAttribute(name) {
            if (name === 'data-type-id') return String(typeId);
            if (name === 'data-final-output') return isFinalOutput ? 'true' : null;
            return null;
        },
        setAttribute() {},
        querySelector(selector) {
            if (selector === '.fuzzwork-price') return fuzzInput;
            if (selector === '.sale-price-unit') return saleInput;
            if (selector === '.real-price') return realInput;
            return null;
        },
        remove() {
            this._removed = true;
        },
    };
}

const buyRow = makeRow(34, false);
const outputRow = makeRow(12005, true);
const tableBody = {
    querySelector(selector) {
        if (selector === 'tr[data-type-id]') return buyRow;
        return null;
    },
    querySelectorAll(selector) {
        if (selector === 'tr[data-type-id]') return [buyRow, outputRow];
        if (selector === 'tr[data-type-id]:not([data-final-output="true"])') return [buyRow];
        if (selector === 'tr[data-market-group-row="true"]') return [];
        return [];
    },
    insertBefore(row) {
        row.parentElement = tableBody;
    },
    appendChild(row) {
        row.parentElement = tableBody;
    },
};

const context = {
    Promise,
    window: {
        SimulationAPI: {
            getFinancialItems: () => [],
            isTabDirty: () => true,
            setPrice(typeId, priceType, value) {
                context._prices[`${typeId}:${priceType}`] = value;
            },
            markTabClean() {},
        },
        syncFinancialOutputGroupRows() {},
        applyFinancialGroupCollapseVisibility() {},
    },
    _prices: {},
    document: {
        getElementById(id) {
            if (id === 'financialItemsBody') return tableBody;
            return null;
        },
    },
    getSimulationPricesMap: () => new Map(),
    getCraftSourceRequirementRows: () => [{ typeId: 34, typeName: 'Tritanium', quantity: 10, marketGroup: 'Minerals' }],
    updateFinalProductRowFromPayload() {},
    initializeFinancialPlannerChrome() {},
    updateFinancialRow() {},
    buildFinancialRow() {
        throw new Error('buildFinancialRow should not be called in this regression setup');
    },
    syncFinancialGroupRows() {},
    getFinalOutputRows: () => [outputRow],
    syncFinalOutputRowPriceState(row) {
        row._synced = true;
    },
    getFuzzworkPriceFromResponse(prices, typeId) {
        const raw = prices[typeId] ?? prices[String(typeId)];
        if (raw === undefined || raw === null) {
            return { found: false, price: 0 };
        }
        return { found: true, price: Number(raw) || 0 };
    },
    applyFuzzworkPriceInputState(input, price) {
        input.value = (Number(price) || 0).toFixed(2);
    },
    fetchAllPrices(typeIds) {
        context._fetchedTypeIds = [...typeIds];
        return Promise.resolve({ 34: 5.25, 12005: 141500000 });
    },
    recalcFinancials() {
        context._recalcCount = (context._recalcCount || 0) + 1;
    },
    applyFinancialPlannerFilters() {},
    updateFinancialGroupSectionSummaries() {},
};

vm.createContext(context);
const fnSource = extractFunction(source, 'updateFinancialTabFromState', 'applyFinancialPlannerFilters');
vm.runInContext(fnSource, context);

if (typeof context.updateFinancialTabFromState !== 'function') {
    throw new Error('updateFinancialTabFromState was not exposed');
}

Promise.resolve(context.updateFinancialTabFromState()).then(() => {
    const fetched = JSON.stringify(context._fetchedTypeIds || []);
    const expectedFetched = JSON.stringify([34, 12005]);
    if (fetched !== expectedFetched) {
        throw new Error(`Expected fetched type IDs ${expectedFetched}, got ${fetched}`);
    }
    if (buyRow._fuzzInput.value !== '5.25') {
        throw new Error(`Expected buy row fuzzwork 5.25, got ${buyRow._fuzzInput.value}`);
    }
    if (outputRow._fuzzInput.value !== '141500000.00') {
        throw new Error(`Expected output row fuzzwork 141500000.00, got ${outputRow._fuzzInput.value}`);
    }
    if (context._prices['34:fuzzwork'] !== 5.25) {
        throw new Error('Expected SimulationAPI buy-row fuzzwork price to be updated');
    }
    if (context._prices['12005:fuzzwork'] !== 141500000) {
        throw new Error('Expected SimulationAPI output-row fuzzwork price to be updated');
    }
    console.log('craft financial price refresh regression passed');
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
