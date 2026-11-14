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
const saleFees = context.computeMarketFeeAmounts(123456789);
const expected = {
    brokerFee: 2962963,
    salesTax: 4737654,
    safetyTax: 308642,
    total: 8009259,
};
Object.entries(expected).forEach(([key, value]) => {
    if (saleFees[key] !== value) {
        throw new Error(`Expected ${key}=${value}, got ${saleFees[key]}`);
    }
});

// Exercise the real rate lookup as well as the fee arithmetic. A current
// server default must supersede the compatibility literal for old payloads.
const rateContext = {
    getMarketFeeProfiles: () => ({ default_sales_tax_percent: 8.25 }),
    normalizeMarketFeePercent: context.normalizeMarketFeePercent,
};
vm.createContext(rateContext);
vm.runInContext(extractFunction(source, 'getSalesTaxPercentForCharacter', '\nfunction setMarketFeePersistStatus'), rateContext);
if (rateContext.getSalesTaxPercentForCharacter(null) !== 8.25
    || rateContext.getSalesTaxPercentForCharacter({ sales_tax_percent: 0 }) !== 0
    || rateContext.getSalesTaxPercentForCharacter(character) !== 3.8375) {
    throw new Error('Sales tax must use the explicit character rate or the server default');
}
rateContext.getMarketFeeProfiles = () => ({});
if (rateContext.getSalesTaxPercentForCharacter(null) !== 7.5) {
    throw new Error('Historical payloads must retain their original fallback');
}

console.log('craft market fee switch regression passed');
