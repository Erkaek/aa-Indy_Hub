const fs = require('fs');
const path = require('path');
const vm = require('vm');

const filePath = path.join(__dirname, '..', 'indy_hub', 'static', 'indy_hub', 'js', 'craft_bp.js');
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

const context = {
    window: {
        BLUEPRINT_DATA: {
            materials_tree: [
                { type_id: 100, sub_materials: [{ type_id: 200 }] },
                { type_id: 300 },
            ],
            final_outputs: [{ type_id: 400 }],
        },
    },
    CRAFT_BP: { productTypeId: 500 },
    getFinalOutputEntries: () => [{ type_id: 400 }],
};

vm.createContext(context);
const collectTreeSource = extractFunction(source, 'collectTypeIdsFromMaterialsTree', 'collectStartupPriceTypeIds');
vm.runInContext(collectTreeSource, context);
const startupSource = extractFunction(source, 'collectStartupPriceTypeIds', 'getCurrentDecisionsFromDom');
vm.runInContext(startupSource, context);

const collectStartupPriceTypeIds = context.collectStartupPriceTypeIds;
if (typeof collectStartupPriceTypeIds !== 'function') {
    throw new Error('collectStartupPriceTypeIds was not exposed');
}

const result = collectStartupPriceTypeIds({
    visibleTypeIds: ['10', '100', '0', ''],
    payload: context.window.BLUEPRINT_DATA,
    productTypeId: 500,
});

const expected = ['10', '100', '200', '300', '400', '500'];
if (JSON.stringify(result) !== JSON.stringify(expected)) {
    throw new Error(`Expected ${JSON.stringify(expected)}, got ${JSON.stringify(result)}`);
}

const applyPriceStateSource = extractFunction(source, 'applyFetchedPriceStateToRow', 'syncFinalOutputRowPriceState');
const updateManualStateSource = extractFunction(source, 'updatePriceInputManualState', 'escapeHtml');
const applyFuzzworkSource = extractFunction(source, 'applyFuzzworkPriceInputState', 'syncFinalOutputRowPriceState');
vm.runInContext(updateManualStateSource, context);
vm.runInContext(applyFuzzworkSource, context);
vm.runInContext(applyPriceStateSource, context);
if (typeof context.applyFetchedPriceStateToRow !== 'function') {
    throw new Error('applyFetchedPriceStateToRow was not exposed');
}

const fuzzworkInput = { value: '0', classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, removeAttribute() {}, dataset: {}, closest: () => ({ classList: { toggle() {}, add() {}, remove() {} }, querySelectorAll: () => [] }), querySelectorAll: () => [] };
const saleInput = { value: '0', classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, removeAttribute() {}, dataset: {}, closest: () => ({ classList: { toggle() {}, add() {}, remove() {} }, querySelectorAll: () => [] }), querySelectorAll: () => [] };
const rowElement = {
    getAttribute: (name) => name === 'data-final-output' ? 'true' : (name === 'data-type-id' ? '603' : null),
    querySelector: (selector) => {
        if (selector === '.fuzzwork-price') {
            return fuzzworkInput;
        }
        if (selector === '.sale-price-unit') {
            return saleInput;
        }
        return null;
    },
    closest: () => null,
    classList: { toggle() {}, add() {}, remove() {} },
};
context.applyFetchedPriceStateToRow(rowElement, { fuzzwork: 123.45, sale: 67.89 });
if (fuzzworkInput.value !== '123.45') {
    throw new Error('Expected fuzzwork input to be populated');
}
if (saleInput.value !== '67.89') {
    throw new Error('Expected sale input to be populated');
}

console.log('craft price scope regression passed');
