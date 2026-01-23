document.addEventListener('DOMContentLoaded', function () {
    const typeField = document.getElementById('id_webhook_type');
    const corpField = document.getElementById('id_corporations');

    if (!typeField || !corpField) {
        return;
    }

    const corpRow = corpField.closest('.form-row') || corpField.closest('.field-corporations');

    function toggleCorporations() {
        const isMaterial = typeField.value === 'material_exchange';
        if (corpRow) {
            corpRow.style.display = isMaterial ? 'none' : '';
        }
        corpField.disabled = isMaterial;
        if (isMaterial) {
            for (const option of corpField.options) {
                option.selected = false;
            }
        }
    }

    typeField.addEventListener('change', toggleCorporations);
    toggleCorporations();
});
