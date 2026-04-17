/**
 * Xeneon Dashboard JavaScript - Three Section Layout
 * Modern responsive dashboard with left sidebar, center sensors, and right graph
 *
 * Fixes applied:
 *  - apiGet now checks res.ok and throws on HTTP errors
 *  - All catch blocks log warnings instead of silently swallowing errors
 *  - Duplicate click listener removed from updateDeviceList (delegate in setupEventListeners is used)
 *  - scheduleReconnect uses clearTimeout to prevent unbounded timer stacking
 *  - Fan status check is now explicit (=== 1)
 *  - window.close() replaced with /api/shutdown fetch
 *  - Polling skips refreshTelemetry when WebSocket is already connected
 *  - refreshFleet throttled to every 15 seconds
 *  - Canvas buffer dimensions synced to CSS size on init and resize
 *  - telemetryHistory entries pruned when device changes
 */

class XeneonDashboard {
    constructor() {
        this.ws = null;
        this.apiBase = '/api';
        this.activeDevice = null;
        this.activeDeviceInfo = null;
        this.connectionStatus = 'disconnected';
        this.sensorData = {};
        this.selectedGraphMetrics = [];
        this.telemetryHistory = new Map();
        this.maxHistory = 100;
        this.favorites = new Set(); // Store favorite sensor types
        this.favoriteWidgets = new Map(); // Map sensor type to widget element
        this.favoritesInitialized = false; // Track favorites initialization
        this._sensorListenersAttached = false; // Prevent duplicate sensor card listeners

        // Timer handles
        this._reconnectTimer = null;
        this._fleetPollCounter = 0;
        this._fleetPollInterval = 15; // refresh fleet every N poll ticks
        this._deviceDiscoveryTimer = null; // Timer for device discovery retries

        this.config = {
            refreshInterval: 1000,
            reconnectDelay: 3000,
            deviceDiscoveryRetries: 20, // Number of retry attempts
            deviceDiscoveryDelay: 2000, // Delay between retries (2 seconds)
            initialDiscoveryDelay: 5000, // Initial delay before starting discovery (5 seconds)
        };

        this.init();
    }

    init() {
        this._pollTimer = null; // Fix #5: keep handle so polling can be cleared
        this.setupEventListeners();
        this.syncCanvasSize();
        // Start device discovery with retry mechanism after initial delay
        this.startDeviceDiscovery();
        this.startPolling();
        // Fix #2: initFavorites() is called once inside updateDeviceList()
        // on first device load — removed duplicate call from here.
    }

    destroy() {
        // Clean up timers to prevent stacking on re-instantiation
        clearInterval(this._pollTimer);
        clearTimeout(this._reconnectTimer);
        clearTimeout(this._deviceDiscoveryTimer);
        if (this.ws) { this.ws.close(); this.ws = null; }
    }

    // =========================================================
    // CANVAS SIZING
    // =========================================================

    syncCanvasSize() {
        const canvas = document.getElementById('graph-canvas');
        if (!canvas) return;

        const resize = () => {
            // Set the drawing-buffer dimensions to match the CSS layout size
            canvas.width = canvas.offsetWidth;
            canvas.height = canvas.offsetHeight;
        };

        resize();
        window.addEventListener('resize', resize);
    }

    // =========================================================
    // FAVORITE SENSORS MANAGEMENT
    // =========================================================

    initFavorites() {
        // Load favorites from localStorage
        const savedFavorites = localStorage.getItem('xeneon_favorites');
        if (savedFavorites) {
            try {
                const favoritesArray = JSON.parse(savedFavorites);
                this.favorites = new Set(favoritesArray);
            } catch (e) {
                console.warn('Failed to parse saved favorites:', e);
                this.favorites = new Set();
            }
        }

        // Set up event listeners for sensor cards
        this.setupSensorCardListeners();

        // Set up favorite controls
        this.setupFavoriteControls();

        // Render initial favorites
        this.renderFavorites();
    }

    setupSensorCardListeners() {
        // Prevent attaching duplicate listeners
        if (this._sensorListenersAttached) return;
        
        // Use event delegation for sensor cards
        const sensorGrid = document.querySelector('.sensor-grid');
        if (!sensorGrid) {
            console.warn('Sensor grid not found, retrying in 100ms');
            setTimeout(() => this.setupSensorCardListeners(), 100);
            return;
        }
        
        this._sensorListenersAttached = true;
        sensorGrid.addEventListener('click', (e) => {
            // First try to find a specific sensor item
            const sensorItem = e.target.closest('.telemetry-item');
            if (sensorItem && sensorItem.dataset.sensorKey) {
                const sensorKey = sensorItem.dataset.sensorKey;
                console.log('Specific sensor item clicked:', sensorKey);
                // Prevent event from bubbling up and triggering multiple clicks
                e.stopPropagation();
                e.preventDefault();
                this.toggleFavorite(sensorKey);
                return;
            }
            
            // Fall back to card-level click (for category favorites)
            const sensorCard = e.target.closest('.sensor-card');
            if (sensorCard) {
                const sensorType = sensorCard.dataset.sensorType;
                console.log('Sensor card clicked:', sensorType);
                if (sensorType) {
                    // Prevent event from bubbling up and triggering multiple clicks
                    e.stopPropagation();
                    e.preventDefault();
                    this.toggleFavorite(sensorType);
                }
            }
        });
    }

    setupFavoriteControls() {
        const clearBtn = document.getElementById('btn-clear-favorites');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                this.clearAllFavorites();
            });
        }
    }

    toggleFavorite(sensorType) {
        console.log('toggleFavorite called with:', sensorType);
        console.log('Current favorites:', Array.from(this.favorites));
        
        if (this.favorites.has(sensorType)) {
            this.favorites.delete(sensorType);
            this.removeFavoriteWidget(sensorType);
            console.log('Removed favorite:', sensorType);
        } else {
            // Limit to 6 favorites (3x2 grid)
            if (this.favorites.size >= 6) {
                alert('Maximum of 6 favorite sensors allowed');
                return;
            }
            this.favorites.add(sensorType);
            console.log('Added favorite:', sensorType);
            this.addFavoriteWidget(sensorType);
        }
        console.log('Updated favorites:', Array.from(this.favorites));
        this.saveFavorites();
        this.updateEmptyState();
    }

    addFavoriteWidget(sensorType) {
        const grid = document.getElementById('favorites-grid');
        if (!grid) {
            console.warn('favorites-grid element not found, cannot add widget');
            return;
        }
        console.log('Adding favorite widget for:', sensorType);

        // Remove existing widget if it exists
        this.removeFavoriteWidget(sensorType);

        const widget = document.createElement('div');
        widget.className = 'favorite-widget';
        widget.dataset.sensorType = sensorType;

        // Get display name for the sensor type
        const displayName = this.getSensorDisplayName(sensorType);

        widget.innerHTML = `
            <div class="favorite-widget-header">
                <span class="favorite-widget-title">${displayName}</span>
                <button class="favorite-widget-remove" data-action="remove">✕</button>
            </div>
            <div class="favorite-widget-content">
                <span class="favorite-widget-value" id="fav-value-${sensorType}">--</span>
                <span class="favorite-widget-unit" id="fav-unit-${sensorType}">--</span>
            </div>
            <div class="favorite-widget-graph">
                <canvas id="fav-graph-${sensorType}" width="100" height="60"></canvas>
            </div>
        `;

        grid.appendChild(widget);
        console.log('Widget appended to DOM for:', sensorType);

        // Set up remove button listener
        const removeBtn = widget.querySelector('.favorite-widget-remove');
        if (removeBtn) {
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleFavorite(sensorType);
            });
        }

        // Store reference and sync canvas size
        this.favoriteWidgets.set(sensorType, widget);
        this.syncFavoriteCanvasSize(sensorType);

        // Initial render
        this.updateFavoriteWidget(sensorType);
    }

    removeFavoriteWidget(sensorType) {
        const widget = this.favoriteWidgets.get(sensorType);
        if (widget && widget.parentNode) {
            widget.parentNode.removeChild(widget);
            this.favoriteWidgets.delete(sensorType);
        }
    }

    updateFavoriteWidget(sensorType) {
        const widget = this.favoriteWidgets.get(sensorType);
        if (!widget) return;

        const valueEl = widget.querySelector(`#fav-value-${sensorType}`);
        const unitEl  = widget.querySelector(`#fav-unit-${sensorType}`);
        const canvas  = widget.querySelector(`#fav-graph-${sensorType}`);

        if (!valueEl || !unitEl || !canvas) return;

        const { value, unit } = this.getSensorValueAndUnit(sensorType);

        // Fix #10: only repaint the canvas when the displayed value has changed
        const valueStr = String(value);
        if (valueEl.textContent !== valueStr) {
            valueEl.textContent = valueStr;
            unitEl.textContent  = unit;
            this.renderFavoriteGraph(sensorType, canvas);
        }
    }

    renderFavoriteGraph(sensorType, canvas) {
        if (!canvas) return;

        const ctx = canvas.getContext('2d');

        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Get history data for this sensor type
        const history = this.getSensorHistory(sensorType);
        if (!history || history.length === 0) return;

        // Draw background
        ctx.fillStyle = 'rgba(255, 255, 255, 0.1)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        // Draw grid lines
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = 0; i <= 5; i++) {
            const y = (canvas.height / 5) * i;
            ctx.moveTo(0, y);
            ctx.lineTo(canvas.width, y);
        }
        ctx.stroke();

        const values = history.map(entry => entry.value);
        const maxVal = Math.max(...values);
        const minVal = Math.min(...values);
        const range  = maxVal - minVal || 1;

        // Fix #3: when there is only one data point, place it in the centre
        // instead of computing x = 0/0 = NaN which breaks the canvas draw.
        const xOf = (index) =>
            values.length > 1
                ? (index / (values.length - 1)) * canvas.width
                : canvas.width / 2;

        ctx.strokeStyle = '#FCE477';
        ctx.lineWidth = 2;
        ctx.beginPath();
        values.forEach((val, index) => {
            const x = xOf(index);
            const y = canvas.height - ((val - minVal) / range) * canvas.height;
            index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        });
        ctx.stroke();

        // Draw last-value dot
        if (values.length > 0) {
            const lastVal = values[values.length - 1];
            const lastY   = canvas.height - ((lastVal - minVal) / range) * canvas.height;
            const lastX   = xOf(values.length - 1);
            ctx.fillStyle = '#FCE477';
            ctx.beginPath();
            ctx.arc(lastX, lastY, 3, 0, 2 * Math.PI);
            ctx.fill();
        }
    }

    syncFavoriteCanvasSize(sensorType) {
        const canvas = document.getElementById(`fav-graph-${sensorType}`);
        if (!canvas) return;

        const resize = () => {
            canvas.width = canvas.offsetWidth;
            canvas.height = canvas.offsetHeight;
            // Re-render after resize
            this.renderFavoriteGraph(sensorType, canvas);
        };

        resize();
        // Note: For simplicity, we're not adding window resize listeners for each canvas
        // The main resize will handle this through the renderFavorites call
    }

    getSensorDisplayName(sensorType) {
        const names = {
            'summary': 'Summary',
            'temperatures': 'Temperatures',
            'fans': 'Fans',
            'power': 'Power',
            'current': 'Current',
            'voltage': 'Voltage',
            'vins': 'Voltage Inputs'
        };
        return names[sensorType] || sensorType;
    }

    getSensorValueAndUnit(sensorType) {
        // Handle specific sensor keys
        if (this.sensorData[sensorType] !== undefined) {
            const value = this.sensorData[sensorType];
            // Determine unit based on sensor type
            if (sensorType.includes('_Power')) {
                return { value: value.toFixed(1), unit: 'W' };
            } else if (sensorType.includes('_Temp') || sensorType === 'Chip_Temp' || sensorType === 'Ambient_Temp') {
                return { value: value, unit: '°C' };
            } else if (sensorType.includes('_Duty') || sensorType === 'FanExtDuty') {
                return { value: value, unit: '%' };
            } else if (sensorType.includes('_Current')) {
                return { value: value.toFixed(2), unit: 'A' };
            } else if (sensorType.includes('_Voltage')) {
                return { value: value.toFixed(2), unit: 'V' };
            } else if (sensorType.startsWith('VIN_')) {
                return { value: value.toFixed(3), unit: 'V' };
            } else if (sensorType === 'Humidity') {
                return { value: value.toFixed(1), unit: '%' };
            } else if (sensorType === 'Vdd' || sensorType === 'Vref') {
                return { value: value.toFixed(3), unit: 'V' };
            } else if (sensorType === 'Temp_Sensor_1' || sensorType === 'Temp_Sensor_2' || 
                       sensorType === 'Temp_Sensor_3' || sensorType === 'Temp_Sensor_4') {
                return { value: value, unit: '' }; // No unit for temp sensors
            } else {
                return { value: value, unit: '' };
            }
        }
        
        // Handle category fallbacks for backward compatibility
        switch (sensorType) {
            case 'summary':
                return { value: (this.sensorData.SYS_Power || 0).toFixed(1), unit: 'W' };
            case 'temperatures':
                return { value: this.sensorData.Chip_Temp || 0, unit: '°C' };
            case 'fans':
                return { value: this.sensorData.Fan1_Duty || 0, unit: '%' };
            case 'power':
                return { value: (this.sensorData.EPS1_Power || 0).toFixed(2), unit: 'W' };
            case 'current':
                return { value: (this.sensorData.EPS1_Current || 0).toFixed(2), unit: 'A' };
            case 'voltage':
                return { value: (this.sensorData.EPS1_Voltage || 0).toFixed(2), unit: 'V' };
            case 'vins':
                return { value: (this.sensorData.VIN_0 || 0).toFixed(3), unit: 'V' };
            default:
                return { value: '--', unit: '' };
        }
    }

    getSensorHistory(sensorType) {
        // Return history for specific sensor keys
        if (this.telemetryHistory.has(sensorType)) {
            return this.telemetryHistory.get(sensorType);
        }
        
        // Handle category fallbacks for backward compatibility
        if (sensorType === 'summary' && this.telemetryHistory.has('SYS_Power')) {
            return this.telemetryHistory.get('SYS_Power');
        }
        
        // Return empty array if no history found
        return [];
    }

    renderFavorites() {
        const grid = document.getElementById('favorites-grid');
        if (!grid) return;

        // Clear existing widgets
        grid.innerHTML = '';

        // Render each favorite
        this.favorites.forEach(sensorType => {
            this.addFavoriteWidget(sensorType);
        });

        this.updateEmptyState();
    }

    updateEmptyState() {
        const emptyMsg = document.getElementById('favorites-empty');
        const grid = document.getElementById('favorites-grid');
        
        console.log('updateEmptyState called, favorites size:', this.favorites.size);
        
        if (this.favorites.size === 0) {
            if (emptyMsg) emptyMsg.style.display = 'flex';
            if (grid) grid.style.display = 'none';
        } else {
            if (emptyMsg) emptyMsg.style.display = 'none';
            if (grid) grid.style.display = 'grid';
        }
    }

    clearAllFavorites() {
        if (confirm('Clear all favorite sensors?')) {
            this.favorites.clear();
            this.favoriteWidgets.clear();
            this.renderFavorites();
            this.saveFavorites();
        }
    }

    saveFavorites() {
        localStorage.setItem('xeneon_favorites', JSON.stringify(Array.from(this.favorites)));
    }

    // =========================================================
    // DEVICE SELECTION (LEFT SIDEBAR)
    // =========================================================

    selectDevice(deviceUid) {
        // Clear history when switching devices so stale data doesn't bleed across
        if (this.activeDevice !== deviceUid) {
            this.telemetryHistory.clear();
        }
        this.activeDevice = deviceUid;
        // Fix #7: only connect once we have a real device UID — never fall back to 'all'
        if (deviceUid) {
            this.connectToWebSocket();
        }
        this.updateDeviceSelection();
    }

    updateDeviceSelection() {
        document.querySelectorAll('.device-item').forEach(item => {
            item.classList.remove('selected');
        });

        const selectedItem = document.querySelector(`.device-item[data-uid="${this.activeDevice}"]`);
        if (selectedItem) {
            selectedItem.classList.add('selected');
        }
    }

    // =========================================================
    // EVENT LISTENERS
    // =========================================================

    setupEventListeners() {
        // Delegated device selection — no per-item listeners needed
        const fleetDeviceList = document.getElementById('fleet-device-list');
        if (fleetDeviceList) {
            fleetDeviceList.addEventListener('click', (e) => {
                const deviceItem = e.target.closest('.device-item');
                if (deviceItem) {
                    const deviceUid = deviceItem.dataset.uid;
                    if (deviceUid) {
                        this.selectDevice(deviceUid);
                    }
                }
            });
        } else {
            console.warn('fleet-device-list element not found, device selection disabled');
        }

        // Graph controls
        const clearGraphBtn = document.getElementById('btn-clear-graph');
        if (clearGraphBtn) {
            clearGraphBtn.addEventListener('click', () => {
                this.clearGraph();
            });
        } else {
            console.warn('btn-clear-graph element not found, graph clearing disabled');
        }

        // Shutdown button — window.close() is blocked by browsers; call the server instead
        const shutdownBtn = document.getElementById('btn-shutdown');
        if (shutdownBtn) {
            shutdownBtn.addEventListener('click', async () => {
                if (confirm('Shutdown dashboard?')) {
                    try {
                        await fetch('/api/shutdown', { method: 'POST' });
                    } catch (e) {
                        // Server may close before responding — that is expected
                    }
                    document.body.innerHTML = '<p style="color:#FCE477;font-family:monospace;padding:2rem">Dashboard shut down.</p>';
                }
            });
        } else {
            console.warn('btn-shutdown element not found, shutdown disabled');
        }
    }

    clearGraph() {
        const canvas = document.getElementById('graph-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            // Use the drawing-buffer dimensions, not the CSS dimensions
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
    }

    // =========================================================
    // WEBSOCKET
    // =========================================================

    connectToWebSocket() {
        // Fix #7: never connect until we have a real device UID
        if (!this.activeDevice) return;

        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }

        try {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${proto}//${location.host}/api/device/${this.activeDevice}/stream`;
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                this.setConnectionStatus('connected');
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleTelemetryUpdate(data);
                } catch (e) {
                    console.warn('WebSocket message parse error:', e);
                }
            };

            this.ws.onclose = () => {
                this.setConnectionStatus('disconnected');
                this.scheduleReconnect();
            };

            this.ws.onerror = (e) => {
                console.warn('WebSocket error:', e);
                this.setConnectionStatus('error');
            };

        } catch (e) {
            console.warn('WebSocket connection failed:', e);
            this.setConnectionStatus('disconnected');
        }
    }

    scheduleReconnect() {
        // Cancel any pending reconnect before scheduling a new one
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => {
            if (this.connectionStatus !== 'connected' && this.activeDevice) {
                this.connectToWebSocket();
            }
        }, this.config.reconnectDelay);
    }

    // =========================================================
    // POLLING
    // =========================================================

    startPolling() {
        // Fix #5: store handle so it can be cleared in destroy()
        this._pollTimer = setInterval(() => {
            // Fleet list changes infrequently — poll it every 15 ticks (~15 s)
            this._fleetPollCounter++;
            if (this._fleetPollCounter >= this._fleetPollInterval) {
                this._fleetPollCounter = 0;
                this.refreshFleet();
            }

            // Only fall back to HTTP telemetry polling when WebSocket is not live
            if (this.activeDevice && this.connectionStatus !== 'connected') {
                this.refreshTelemetry();
            }
            // Fix #6: timestamp is updated in handleTelemetryUpdate() only when
            // real data arrives — removed from here so it doesn't tick while disconnected.
        }, this.config.refreshInterval);
    }

    // =========================================================
    // API
    // =========================================================

    async apiGet(endpoint) {
        const res = await fetch(`${this.apiBase}${endpoint}`);
        if (!res.ok) {
            throw new Error(`HTTP ${res.status} for ${endpoint}`);
        }
        return res.json();
    }

    async refreshFleet() {
        try {
            const devices = await this.apiGet('/devices');
            this.updateDeviceList(devices);
        } catch (e) {
            console.warn('refreshFleet failed:', e);
        }
    }

    startDeviceDiscovery() {
        // Clear any existing timer
        if (this._deviceDiscoveryTimer) {
            clearTimeout(this._deviceDiscoveryTimer);
            this._deviceDiscoveryTimer = null;
        }

        // Add initial delay to give DataSourceManager time to initialize
        console.log(`Starting device discovery with ${this.config.initialDiscoveryDelay}ms initial delay...`);
        
        this._deviceDiscoveryTimer = setTimeout(() => {
            console.log('Initial delay complete, starting device discovery retry mechanism');
            // Fix #4: start at 0; _attemptDeviceDiscovery increments before use
            this._deviceDiscoveryAttempts = 0;
            this._attemptDeviceDiscovery();
        }, this.config.initialDiscoveryDelay);
    }

    async _attemptDeviceDiscovery() {
        // Fix #4: increment first so logs and comparisons are consistent
        this._deviceDiscoveryAttempts++;
        const attempt = this._deviceDiscoveryAttempts;
        const max = this.config.deviceDiscoveryRetries;

        try {
            console.log(`Device discovery attempt ${attempt}/${max}`);
            const devices = await this.apiGet('/devices');

            console.log('API response:', devices);

            if (devices && devices.length > 0) {
                // Success - devices found
                console.log(`Device discovery successful: ${devices.length} device(s) found`);
                this.updateDeviceList(devices);
                this._deviceDiscoveryTimer = null;
                return;
            } else if (attempt < max) {
                console.log(`No devices yet (attempt ${attempt}/${max}), retrying in ${this.config.deviceDiscoveryDelay}ms`);
                this._deviceDiscoveryTimer = setTimeout(() => {
                    this._attemptDeviceDiscovery();
                }, this.config.deviceDiscoveryDelay);
            } else {
                console.warn(`Device discovery failed after ${max} attempts`);
                this._deviceDiscoveryTimer = null;
                this.updateDeviceList([]);
            }
        } catch (e) {
            console.warn(`Device discovery attempt ${attempt} failed:`, e);

            if (attempt < max) {
                this._deviceDiscoveryTimer = setTimeout(() => {
                    this._attemptDeviceDiscovery();
                }, this.config.deviceDiscoveryDelay);
            } else {
                console.warn(`Device discovery failed after ${max} attempts`);
                this._deviceDiscoveryTimer = null;
                this.updateDeviceList([]);
            }
        }
    }

    async refreshTelemetry() {
        try {
            const telemetry = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
            this.handleTelemetryUpdate(telemetry);
        } catch (e) {
            console.warn('refreshTelemetry failed:', e);
        }

        try {
            this.activeDeviceInfo = await this.apiGet(`/device/${this.activeDevice}/info`);
            this.updateFooterInfo();
        } catch (e) {
            console.warn('refreshDeviceInfo failed:', e);
        }
    }

    // =========================================================
    // DATA HANDLING
    // =========================================================

    handleTelemetryUpdate(data) {
        this.sensorData = data;

        // Fix #11: remove keys that are no longer present in the new snapshot
        // so stale sensors don't accumulate in telemetryHistory forever.
        for (const key of this.telemetryHistory.keys()) {
            if (!(key in data)) {
                this.telemetryHistory.delete(key);
            }
        }

        for (const [key, value] of Object.entries(data)) {
            if (!this.telemetryHistory.has(key)) {
                this.telemetryHistory.set(key, []);
            }
            const history = this.telemetryHistory.get(key);
            history.push({ timestamp: Date.now(), value });
            if (history.length > this.maxHistory) {
                history.shift();
            }
        }

        // Fix #6: only mark "last update" when real data actually arrives
        const el = document.getElementById('last-update');
        if (el) el.textContent = `Last update: ${new Date().toLocaleTimeString()}`;

        this.renderCards();
    }

    // =========================================================
    // FLEET / DEVICE LIST
    // =========================================================

    updateDeviceList(devices) {
        const listEl = document.getElementById('fleet-device-list');
        if (!listEl) return;

        listEl.innerHTML = '';

        if (!devices || devices.length === 0) {
            // Check if we're still in discovery mode
            if (this._deviceDiscoveryTimer) {
                listEl.innerHTML = `
                    <div class="device-item placeholder">
                        <div class="device-info">
                            <span class="device-name">Scanning for devices...</span>
                            <span class="device-details">Attempt ${this._deviceDiscoveryAttempts}/${this.config.deviceDiscoveryRetries}</span>
                        </div>
                    </div>
                `;
            } else {
                listEl.innerHTML = `
                    <div class="device-item placeholder">
                        <div class="device-info">
                            <span class="device-name">No devices found</span>
                            <span class="device-details">Scanning for Benchlab devices...</span>
                        </div>
                    </div>
                `;
            }
            return;
        }

        devices.forEach(device => {
            const item = document.createElement('div');
            item.className = 'device-item';
            item.dataset.uid = device.uid;

            // No per-item addEventListener — handled by the delegated listener in setupEventListeners
            item.innerHTML = `
                <div class="device-info">
                    <span class="device-name">Port: ${device.port} | UID: ${device.uid}</span>
                    <span class="device-details">Firmware: ${device.firmware || 'unknown'}</span>
                </div>
                <span class="device-status connected">CONNECTED</span>
            `;

            listEl.appendChild(item);
        });

        // Re-apply selected highlight after list rebuild
        this.updateDeviceSelection();
        
        // Initialize favorites system after first device list load
        if (!this.favoritesInitialized) {
            this.favoritesInitialized = true;
            this.initFavorites();
        }
    }

    // =========================================================
    // SENSOR CARDS
    // =========================================================

    // =========================================================
    // SENSOR CARDS — keyed in-place updates (Fix #9)
    // Instead of obliterating and recreating the DOM every second,
    // we build the skeleton once and only patch .textContent when
    // a value has actually changed.
    // =========================================================

    /**
     * Ensure a container holds exactly the supplied rows (keyed by data-sensor-key).
     * Rows that don't exist yet are created; rows whose value text is unchanged are
     * left untouched (no layout thrash, no broken CSS transitions).
     *
     * @param {HTMLElement} container  – the .card-content div
     * @param {Array<{key:string, label:string, text:string}>} rows
     */
    _ensureRows(container, rows) {
        rows.forEach(({ key, label, text }) => {
            let item = container.querySelector(`.telemetry-item[data-sensor-key="${key}"]`);
            if (!item) {
                item = document.createElement('div');
                item.className = 'telemetry-item';
                item.dataset.sensorKey = key;
                item.innerHTML =
                    `<span class="telemetry-label">${label}</span>` +
                    `<span class="telemetry-value"></span>`;
                container.appendChild(item);
            }
            const valEl = item.querySelector('.telemetry-value');
            if (valEl && valEl.textContent !== text) valEl.textContent = text;
        });
    }

    renderCards() {
        this.renderSummaryCard();
        this.renderTemperaturesCard();
        this.renderFansCard();
        this.renderPowerCard();
        this.renderCurrentCard();
        this.renderVoltageCard();
        this.renderVinsCard();

        // Update favorite widgets with new data
        this.favorites.forEach(sensorType => {
            this.updateFavoriteWidget(sensorType);
        });
    }

    renderSummaryCard() {
        const el = document.getElementById('card-summary');
        if (!el) return;
        const d = this.sensorData;
        this._ensureRows(el, [
            { key: 'SYS_Power', label: 'SYS', text: `${(d.SYS_Power || 0).toFixed(1)} W` },
            { key: 'CPU_Power', label: 'CPU', text: `${(d.CPU_Power || 0).toFixed(1)} W` },
            { key: 'GPU_Power', label: 'GPU', text: `${(d.GPU_Power || 0).toFixed(1)} W` },
            { key: 'MB_Power',  label: 'MB',  text: `${(d.MB_Power  || 0).toFixed(1)} W` },
        ]);
    }

    renderTemperaturesCard() {
        const el = document.getElementById('card-temperatures');
        if (!el) return;
        const d = this.sensorData;
        this._ensureRows(el, [
            { key: 'Chip_Temp',    label: 'Chip',    text: `${d.Chip_Temp    || 0}°C` },
            { key: 'Ambient_Temp', label: 'Ambient', text: `${d.Ambient_Temp || 0}°C` },
            { key: 'Humidity',     label: 'Humidity',text: `${(d.Humidity    || 0).toFixed(1)}%` },
            { key: 'Temp_Sensor_1', label: 'S1', text: `${d.Temp_Sensor_1 ?? 'N/A'}` },
            { key: 'Temp_Sensor_2', label: 'S2', text: `${d.Temp_Sensor_2 ?? 'N/A'}` },
            { key: 'Temp_Sensor_3', label: 'S3', text: `${d.Temp_Sensor_3 ?? 'N/A'}` },
            { key: 'Temp_Sensor_4', label: 'S4', text: `${d.Temp_Sensor_4 ?? 'N/A'}` },
        ]);
    }

    renderFansCard() {
        const el = document.getElementById('card-fans');
        if (!el) return;
        const d = this.sensorData;
        const rows = [];
        for (let i = 1; i <= 9; i++) {
            const duty   = d[`Fan${i}_Duty`]   || 0;
            const rpm    = d[`Fan${i}_RPM`]    || 0;
            const status = d[`Fan${i}_Status`] === 1 ? 'ON' : 'OFF';
            rows.push({ key: `Fan${i}_Duty`, label: `Fan${i}`, text: `${duty}% | ${rpm} RPM | ${status}` });
        }
        rows.push({ key: 'FanExtDuty', label: 'Ext Fan Duty', text: `${d.FanExtDuty || 0}%` });
        this._ensureRows(el, rows);
    }

    renderPowerCard() {
        const el = document.getElementById('card-power');
        if (!el) return;
        const d = this.sensorData;
        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        this._ensureRows(el, rails.map(r => ({
            key: `${r}_Power`, label: r, text: `${(d[`${r}_Power`] || 0).toFixed(2)} W`
        })));
    }

    renderCurrentCard() {
        const el = document.getElementById('card-current');
        if (!el) return;
        const d = this.sensorData;
        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        this._ensureRows(el, rails.map(r => ({
            key: `${r}_Current`, label: r, text: `${(d[`${r}_Current`] || 0).toFixed(2)} A`
        })));
    }

    renderVoltageCard() {
        const el = document.getElementById('card-voltage');
        if (!el) return;
        const d = this.sensorData;
        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        this._ensureRows(el, rails.map(r => ({
            key: `${r}_Voltage`, label: r, text: `${(d[`${r}_Voltage`] || 0).toFixed(2)} V`
        })));
    }

    renderVinsCard() {
        const el = document.getElementById('card-vins');
        if (!el) return;
        const d = this.sensorData;
        // Fix #13: loop 0–11 (12 channels); verify against your firmware spec.
        // The old loop went to 12 (13 entries), producing a VIN_12 that was always 0.
        const rows = [];
        for (let i = 0; i <= 11; i++) {
            rows.push({ key: `VIN_${i}`, label: `VIN_${i}`, text: `${(d[`VIN_${i}`] || 0).toFixed(3)} V` });
        }
        rows.push({ key: 'Vdd',  label: 'Vdd',  text: `${(d.Vdd  || 0).toFixed(3)} V` });
        rows.push({ key: 'Vref', label: 'Vref', text: `${(d.Vref || 0).toFixed(3)} V` });
        this._ensureRows(el, rows);
    }

    // =========================================================
    // STATUS
    // =========================================================

    setConnectionStatus(status) {
        this.connectionStatus = status;
        const indicator = document.getElementById('connection-status');
        const text = document.getElementById('status-text');

        if (indicator) {
            indicator.className = `status-indicator status-${status}`;
        }
        if (text) {
            text.textContent = status.charAt(0).toUpperCase() + status.slice(1);
        }
    }

    updateFooterInfo() {
        const footerInfo = document.getElementById('footer-info');
        if (!footerInfo || !this.activeDeviceInfo) return;

        const parts = [
            `Port: ${this.activeDeviceInfo.port || 'N/A'}`,
            `UID: ${this.activeDevice || 'N/A'}`,
            `FW: 0x${(this.activeDeviceInfo.FwVersion || 0).toString(16).toUpperCase()}`
        ];

        footerInfo.textContent = parts.join(' | ');
    }
}

// =========================================================
// INIT
// =========================================================

document.addEventListener('DOMContentLoaded', () => {
    window.xeneonDashboard = new XeneonDashboard();
});