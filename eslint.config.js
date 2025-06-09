module.exports = [
    {
        languageOptions: {
            globals: {
                // Browser globals
                window: 'readonly',
                document: 'readonly',
                console: 'readonly',
                // ES2021 globals
                globalThis: 'readonly',
            },
            ecmaVersion: 12,
            sourceType: 'module',
        },
        rules: {
            // Add your custom rules here
        },
    }
];
