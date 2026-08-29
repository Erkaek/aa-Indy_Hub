const fs = require('fs');
const path = require('path');
const vm = require('vm');

const filePath = path.join(
    __dirname,
    '..',
    'indy_hub',
    'static',
    'indy_hub',
    'js',
    'craft_bp.js'
);
const source = fs.readFileSync(filePath, 'utf8');

function extractFunction(sourceText, functionName, nextMarker) {
    const start = sourceText.indexOf(`function ${functionName}`);
    const end = sourceText.indexOf(nextMarker, start);
    if (start === -1 || end === -1) {
        throw new Error(`Unable to extract ${functionName}`);
    }
    return sourceText.slice(start, end);
}

const character = { character_id: 9001001, sales_tax_percent: 3.8375 };
const context = {
    state: {
        purpose: 'personal_use',
        sellerCharacterId: character.character_id,
        brokerFeePercent: 2.4,
        safetyTaxPercent: 0.25,
    },
    getMarketFeeState() {
        return context.state;
    },
    getMarketFeeCharacter(characterId) {
        return Number(characterId) === character.character_id ? character : null;
    },
    normalizeMarketFeePercent(value) {
        return Math.min(100, Math.max(0, Number(value) || 0));
    },
    getSalesTaxPercentForCharacter(value) {
        return Number(value?.sales_tax_percent || 0);
    },
};

vm.createContext(context);
vm.runInContext(
    `const MARKET_PURPOSE_SALE = 'market_sale';\n${extractFunction(
        source,
        'computeMarketFeeAmounts',
        '// ---------------------------------------------------------------------------\n// Revenue mode'
    )}`,
    context
);

const personalFees = context.computeMarketFeeAmounts(100000000);
if (personalFees.total !== 0) {
    throw new Error(`Personal-use fees must be zero, got ${personalFees.total}`);
}

context.state.purpose = 'market_sale';
const saleFees = context.computeMarketFeeAmounts(100000000);
const expected = {
    brokerFee: 2400000,
    salesTax: 3837500,
    safetyTax: 250000,
    total: 6487500,
};
Object.entries(expected).forEach(([key, value]) => {
    if (saleFees[key] !== value) {
        throw new Error(`Expected ${key}=${value}, got ${saleFees[key]}`);
    }
});

console.log('craft market fee switch regression passed');
