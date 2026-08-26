document.addEventListener('DOMContentLoaded', () => {
    const inputGrid = document.getElementById('grid-body');
    const btnAddRow = document.getElementById('btn-add-row');
    const varianceForm = document.getElementById('variance-form');
    const btnAnalyze = document.getElementById('btn-analyze');

    if (btnAddRow && inputGrid) {
        btnAddRow.addEventListener('click', () => {
            const newRow = document.createElement('tr');
            newRow.className = 'input-row';
            newRow.innerHTML = `
                <td>
                    <input type="text" name="category" placeholder="e.g. Q4 Revenue" required aria-label="Category">
                </td>
                <td>
                    <input type="number" step="any" name="forecast" placeholder="0.00" required aria-label="Forecast" class="numeric-input">
                </td>
                <td>
                    <input type="number" step="any" name="actual" placeholder="0.00" required aria-label="Actual" class="numeric-input">
                </td>
                <td>
                    <input type="text" name="notes" placeholder="Context..." aria-label="Notes">
                </td>
                <td class="action-cell">
                    <button type="button" class="btn-icon btn-remove" aria-label="Remove row" title="Remove row">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                    </button>
                </td>
            `;
            
            newRow.style.opacity = '0';
            inputGrid.appendChild(newRow);
            
            requestAnimationFrame(() => {
                newRow.style.transition = 'opacity 0.2s ease-in';
                newRow.style.opacity = '1';
            });
        });

        // Event delegation for remove buttons
        inputGrid.addEventListener('click', (e) => {
            const removeBtn = e.target.closest('.btn-remove');
            if (removeBtn) {
                const row = removeBtn.closest('tr');
                if (inputGrid.children.length > 1) {
                    row.style.opacity = '0';
                    setTimeout(() => row.remove(), 200);
                }
            }
        });
    }

    if (varianceForm && btnAnalyze) {
        varianceForm.addEventListener('submit', () => {
            const btnText = btnAnalyze.querySelector('.btn-text');
            const loader = btnAnalyze.querySelector('.loader');
            
            btnAnalyze.disabled = true;
            btnText.textContent = 'Analyzing...';
            if (loader) loader.classList.remove('hidden');
        });
    }
});