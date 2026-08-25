const fs = require('fs');
const path = require('path');
const vm = require('vm');

const templatePath = path.join(
    __dirname,
    '..',
    'indy_hub',
    'templates',
    'indy_hub',
    'material_exchange',
    'sell.html'
);
const source = fs.readFileSync(templatePath, 'utf8');

function extractParserSource(sourceText) {
    const startMarker = '    function collapseWhitespace(value) {';
    const endMarker = '    function renderPasteList(';
    const startIndex = sourceText.indexOf(startMarker);
    const endIndex = sourceText.indexOf(endMarker, startIndex);
    if (startIndex === -1 || endIndex === -1) {
        throw new Error('Material Exchange sell paste parser functions were not found');
    }
    return sourceText.slice(startIndex, endIndex);
}

const context = {
    sellPasteCatalog: new Map([
        ['bitumens', { type_name: 'Bitumens' }],
        ['coesite', { type_name: 'Coesite' }],
        ['sylvite', { type_name: 'Sylvite' }],
        ['zeolites', { type_name: 'Zeolites' }],
    ]),
    window: {
        getIndyHubLocale: () => 'en',
    },
};

vm.createContext(context);
vm.runInContext(extractParserSource(source), context);

if (typeof context.buildPasteEntries !== 'function') {
    throw new Error('buildPasteEntries was not exposed');
}

function entriesFor(text) {
    return JSON.parse(JSON.stringify(context.buildPasteEntries(text).entries));
}

function assertEntries(label, actual, expected) {
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        throw new Error(
            `${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`
        );
    }
}

assertEntries(
    'tab-separated Sylvite variants remain distinct',
    entriesFor('Compressed Sylvite\t2390\nCompressed Brimful Sylvite\t8281'),
    [
        {
            key: 'compressed sylvite',
            label: 'Compressed Sylvite',
            quantity: 2390,
        },
        {
            key: 'compressed brimful sylvite',
            label: 'Compressed Brimful Sylvite',
            quantity: 8281,
        },
    ]
);

assertEntries(
    'space-separated ore variants remain distinct',
    entriesFor([
        'Compressed Brimful Bitumens 3298',
        'Compressed Brimful Coesite 111',
        'Compressed Brimful Sylvite 8281',
        'Compressed Brimful Zeolites 128',
        'Compressed Coesite 1390',
        'Compressed Sylvite 2390',
        'Compressed Zeolites 2801',
        'Glistening Bitumens 22',
        'Glistening Coesite 66',
    ].join('\n')),
    [
        { key: 'compressed brimful bitumens', label: 'Compressed Brimful Bitumens', quantity: 3298 },
        { key: 'compressed brimful coesite', label: 'Compressed Brimful Coesite', quantity: 111 },
        { key: 'compressed brimful sylvite', label: 'Compressed Brimful Sylvite', quantity: 8281 },
        { key: 'compressed brimful zeolites', label: 'Compressed Brimful Zeolites', quantity: 128 },
        { key: 'compressed coesite', label: 'Compressed Coesite', quantity: 1390 },
        { key: 'compressed sylvite', label: 'Compressed Sylvite', quantity: 2390 },
        { key: 'compressed zeolites', label: 'Compressed Zeolites', quantity: 2801 },
        { key: 'glistening bitumens', label: 'Glistening Bitumens', quantity: 22 },
        { key: 'glistening coesite', label: 'Glistening Coesite', quantity: 66 },
    ]
);

assertEntries(
    'identical exact types still aggregate',
    entriesFor('Compressed Sylvite 2390\nCompressed Sylvite 10'),
    [
        {
            key: 'compressed sylvite',
            label: 'Compressed Sylvite',
            quantity: 2400,
        },
    ]
);

assertEntries(
    'tab-separated base item metadata parsing remains supported',
    entriesFor('Sylvite\t2,390\t504.81 ISK'),
    [
        {
            key: 'sylvite',
            label: 'Sylvite',
            quantity: 2390,
        },
    ]
);

assertEntries(
    'quantity-first known base item parsing remains supported',
    entriesFor('2,390 Sylvite'),
    [
        {
            key: 'sylvite',
            label: 'Sylvite',
            quantity: 2390,
        },
    ]
);

console.log('material exchange sell paste parser regression passed');
