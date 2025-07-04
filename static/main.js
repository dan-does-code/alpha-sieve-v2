document.addEventListener('DOMContentLoaded', () => {

    // --- GLOBAL STATE ---
    let config = { api_key: '', starred_models: [], all_models: [], selected_model: '' };
    const sessionState = { walletsArray: [], selectedGoodWallets: new Set() };

    // --- UI ELEMENT REFERENCES ---
    const apiKeyInput = document.getElementById('api-key');
    const saveApiKeyBtn = document.getElementById('save-api-key');
    const apiKeyStatus = document.getElementById('api-key-status');
    const modelSelector = document.getElementById('model-selector');
    const refreshModelsBtn = document.getElementById('refresh-models');
    const promptSelector = document.getElementById('prompt-selector');
    const fileUpload = document.getElementById('file-upload');
    const parseFilesBtn = document.getElementById('parse-files-btn');
    const walletListContainer = document.getElementById('wallet-list-container');
    const goodWalletsBulkInput = document.getElementById('good-wallets-bulk-input');
    const learnFilterBtn = document.getElementById('learn-filter-btn');
    const codeInput = document.getElementById('code-input');
    const executeCodeBtn = document.getElementById('execute-code-btn');
    const copyCodeBtn = document.getElementById('copy-code-btn');
    const saveCodeBtn = document.getElementById('save-code-btn');
    const clearCodeBtn = document.getElementById('clear-code-btn');
    const resultsOutput = document.getElementById('results-output');

    // --- CORE LOGIC & EVENT LISTENERS ---

    saveApiKeyBtn.addEventListener('click', async () => {
        const newKey = apiKeyInput.value.trim();
        if (!newKey) return;
        setApiKeyStatus('loading');
        try {
            const response = await fetch('/api/validate-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: newKey })
            });
            const data = await response.json();
            if (response.ok && data.valid) {
                config.api_key = newKey;
                await saveConfig();
                setApiKeyStatus('success');
            } else {
                throw new Error(data.error || 'Unknown validation error');
            }
        } catch (error) {
            setApiKeyStatus('error');
            alert('API Key validation failed: ' + error.message);
        }
    });

    refreshModelsBtn.addEventListener('click', async () => {
        if (!config.api_key) {
            alert('Please save a valid API key first.');
            return;
        }
        const btn = refreshModelsBtn;
        const originalText = btn.textContent;
        btn.textContent = 'Refreshing...';
        btn.disabled = true;
        try {
            const response = await fetch('/api/models');
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Failed to fetch models from API');
            }
            const models = await response.json();
            config.all_models = models;
            await saveConfig();
            alert('Model list refreshed and saved.');
        } catch (error) {
            alert('Error refreshing models: ' + error.message);
        } finally {
            btn.textContent = originalText;
            btn.disabled = false;
        }
    });
    
    modelSelector.addEventListener('change', async () => {
        const newModel = modelSelector.value;
        if (newModel && config.selected_model !== newModel) {
            config.selected_model = newModel;
            await saveConfig();
        }
    });

    parseFilesBtn.addEventListener('click', async () => {
        if (fileUpload.files.length === 0) {
            alert('Please select one or more Excel files.');
            return;
        }
        const formData = new FormData();
        for (const file of fileUpload.files) {
            formData.append('files', file);
        }
        try {
            const response = await fetch('/api/parse-files', { method: 'POST', body: formData });
            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || 'Unknown parsing error');
            }
            const data = await response.json();
            sessionState.walletsArray = data.wallets;
            displayWallets(data.wallets);
            alert(`${data.wallets.length} total wallets parsed successfully.`);
        } catch (error) {
            alert('Error parsing files: ' + error.message);
        }
    });
    
    goodWalletsBulkInput.addEventListener('input', () => {
        const pastedText = goodWalletsBulkInput.value;
        const addresses = pastedText.split(/[\s,;| \n\r]+/).filter(Boolean);
        syncCheckboxesFromAddressList(addresses);
    });

    learnFilterBtn.addEventListener('click', async () => {
        if (sessionState.selectedGoodWallets.size === 0) {
            alert('Select "GOOD" wallets first.');
            return;
        }
        if (!modelSelector.value || !promptSelector.value) {
            alert('Please select a model and a prompt template.');
            return;
        }

        learnFilterBtn.disabled = true;
        learnFilterBtn.textContent = 'Generating...';
        codeInput.value = '';
        resultsOutput.innerHTML = '';

        const selectedWallets = Array.from(sessionState.selectedGoodWallets).map(index => sessionState.walletsArray[index]);
        
        try {
            const response = await fetch('/api/learn-filter-stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    wallets: selectedWallets,
                    model: modelSelector.value,
                    promptTemplate: promptSelector.value
                })
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Server error: ${response.status} ${errorText}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n');
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const jsonStr = line.substring(6);
                            if (jsonStr) {
                                const data = JSON.parse(jsonStr);
                                if (data.error) throw new Error(`Stream error: ${data.error}`);
                                if (data.token) {
                                    codeInput.value += data.token;
                                    codeInput.scrollTop = codeInput.scrollHeight;
                                }
                            }
                        } catch (e) {
                            console.error("Failed to parse stream data chunk:", line, e);
                        }
                    }
                }
            }
        } catch (error) {
            console.error('Learn Filter stream failed:', error);
            alert('An error occurred during generation: ' + error.message);
            codeInput.value += `\n\n# AN ERROR OCCURRED: ${error.message}`;
        } finally {
            learnFilterBtn.disabled = false;
            learnFilterBtn.textContent = 'Learn Filter from Selection';
        }
    });

    executeCodeBtn.addEventListener('click', async () => {
        const code = codeInput.value.trim();
        if (!code) {
            alert('Code input is empty.');
            return;
        }
        try {
            const response = await fetch('/api/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code })
            });
            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || 'Unknown execution error');
            }
            const data = await response.json();
            displayResults(data.results);
            alert(`Execution successful. ${data.results.length} wallets matched the filter.`);
        } catch (error) {
            alert('Code execution failed: ' + error.message);
        }
    });

    copyCodeBtn.addEventListener('click', () => {
        if (!codeInput.value) return;
        navigator.clipboard.writeText(codeInput.value).then(() => alert('Code copied to clipboard!'))
          .catch(err => alert('Failed to copy: ' + err.message));
    });

    saveCodeBtn.addEventListener('click', () => {
        if (!codeInput.value) return;
        const blob = new Blob([codeInput.value], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const ts = new Date().toISOString().replace(/[:.]/g, '-');
        a.download = `snippet_${ts}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });

    clearCodeBtn.addEventListener('click', () => {
        if (codeInput.value && confirm('Are you sure you want to clear the code?')) {
            codeInput.value = '';
        }
    });

    // --- UI RENDERING & HELPER FUNCTIONS ---

    function displayWallets(wallets) {
        walletListContainer.innerHTML = wallets.map((wallet, index) => `
            <li>
                <input type="checkbox" id="wallet-check-${index}" data-index="${index}" data-address="${wallet.wallet_address}">
                <label for="wallet-check-${index}" title="${wallet.wallet_address}">
                    ${wallet.wallet_address ? `${wallet.wallet_address.substring(0, 6)}...${wallet.wallet_address.substring(wallet.wallet_address.length - 6)}` : 'N/A'}
                    | PnL: ${(wallet.performance_and_risk.pnl_sol || 0).toFixed(2)}
                    | ROI: ${(wallet.performance_and_risk.roi_percent || 0).toFixed(1)}%
                </label>
            </li>
        `).join('');

        walletListContainer.addEventListener('change', (event) => {
            if (event.target.type === 'checkbox') {
                const index = parseInt(event.target.dataset.index, 10);
                if (event.target.checked) {
                    sessionState.selectedGoodWallets.add(index);
                } else {
                    sessionState.selectedGoodWallets.delete(index);
                }
                syncBulkInputFromCheckboxes();
            }
        });
    }

    function syncCheckboxesFromAddressList(addresses) {
        sessionState.selectedGoodWallets.clear();
        const addressSet = new Set(addresses.map(a => a.trim()));
        const allCheckboxes = walletListContainer.querySelectorAll('input[type="checkbox"]');
        allCheckboxes.forEach(checkbox => {
            const walletAddress = checkbox.dataset.address;
            const index = parseInt(checkbox.dataset.index, 10);
            checkbox.checked = addressSet.has(walletAddress);
            if (checkbox.checked) {
                sessionState.selectedGoodWallets.add(index);
            }
        });
    }

    function syncBulkInputFromCheckboxes() {
        const selectedAddresses = Array.from(sessionState.selectedGoodWallets)
            .map(index => sessionState.walletsArray[index].wallet_address);
        goodWalletsBulkInput.value = selectedAddresses.join('\n');
    }

    function displayResults(results) {
        if (!results || results.length === 0) {
            resultsOutput.innerHTML = '<li>No wallets matched the filter.</li>';
            return;
        }
        resultsOutput.innerHTML = results.map(w => `<li>${w.wallet_address}</li>`).join('');
    }
    
    function setApiKeyStatus(status) {
        apiKeyStatus.className = 'status-indicator';
        switch (status) {
            case 'success': apiKeyStatus.classList.add('success'); apiKeyStatus.textContent = '✓'; break;
            case 'error': apiKeyStatus.classList.add('error'); apiKeyStatus.textContent = '✗'; break;
            case 'loading': apiKeyStatus.textContent = '...'; break;
            default: apiKeyStatus.textContent = '';
        }
    }

    function populateModelSelector() {
        modelSelector.innerHTML = (config.starred_models || []).map(m =>
            `<option value="${m}" ${m === config.selected_model ? 'selected' : ''}>${m}</option>`
        ).join('');
    }

    async function saveConfig() {
        try {
            await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
        } catch (error) {
            console.error('CRITICAL: Could not save config to server.', error);
        }
    }

    async function init() {
        try {
            const configResponse = await fetch('/api/config');
            if (!configResponse.ok) throw new Error('Could not fetch config.');
            config = await configResponse.json();
            apiKeyInput.value = config.api_key || '';
            populateModelSelector();

            const promptsResponse = await fetch('/api/prompts');
            if (!promptsResponse.ok) throw new Error('Could not fetch prompts.');
            const prompts = await promptsResponse.json();
            promptSelector.innerHTML = prompts.map(p => `<option value="${p}">${p}</option>`).join('');
        } catch (error) {
            console.error('Fatal initialization error:', error);
            alert('Could not initialize app from server: ' + error.message);
        }
    }

    // --- APPLICATION START ---
    init();
});