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
        this.setupEventListeners();
        this.syncCanvasSize();
        // Start device discovery with retry mechanism after initial delay
        this.startDeviceDiscovery();
        this.startPolling();
        // Initialize favorites system after initial setup
        this.initFavorites();
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
        const unitEl = widget.querySelector(`#fav-unit-${sensorType}`);
        const canvas = widget.querySelector(`#fav-graph-${sensorType}`);

        if (!valueEl || !unitEl || !canvas) return;

        // Get current value and unit
        const { value, unit } = this.getSensorValueAndUnit(sensorType);

        // Update value display
        valueEl.textContent = value;
        unitEl.textContent = unit;

        // Update mini-graph
        this.renderFavoriteGraph(sensorType, canvas);
    }

    renderFavoriteGraph(sensorType, canvas) {
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        
        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Get history data for this sensor type
        const history = this.getSensorHistory(sensorType);
        if (!history || history.length === 0) return;

        // Draw background grid
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

        // Prepare data points
        const values = history.map(entry => entry.value);
        const maxVal = Math.max(...values);
        const minVal = Math.min(...values);
        const range = maxVal - minVal || 1; // Avoid division by zero

        // Draw line graph
        ctx.strokeStyle = '#FCE477';
        ctx.lineWidth = 2;
        ctx.beginPath();

        values.forEach((val, index) => {
            const x = (index / (values.length - 1)) * canvas.width;
            // Invert Y because canvas 0 is at top
            const y = canvas.height - ((val - minVal) / range) * canvas.height;
            
            if (index === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });

        ctx.stroke();

        // Draw last value marker
        if (values.length > 0) {
            const lastVal = values[values.length - 1];
            const lastY = canvas.height - ((lastVal - minVal) / range) * canvas.height;
            
            ctx.fillStyle = '#FCE477';
            ctx.beginPath();
            ctx.arc(canvas.width - 5, lastY, 3, 0, 2 * Math.PI);
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
        this.connectToWebSocket();
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
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }

        try {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${proto}//${location.host}/api/device/${this.activeDevice || 'all'}/stream`;
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
        setInterval(() => {
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

            document.getElementById('last-update').textContent =
                `Last update: ${new Date().toLocaleTimeString()}`;

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
            // Start discovery with retry mechanism
            this._deviceDiscoveryAttempts = 0;
            this._attemptDeviceDiscovery();
        }, this.config.initialDiscoveryDelay);
    }

    async _attemptDeviceDiscovery() {
        try {
            console.log(`Device discovery attempt ${this._deviceDiscoveryAttempts + 1}/${this.config.deviceDiscoveryRetries}`);
            const devices = await this.apiGet('/devices');
            
            console.log('API response:', devices);
            
            if (devices && devices.length > 0) {
                // Success - devices found
                console.log(`Device discovery successful: ${devices.length} device(s) found`);
                this.updateDeviceList(devices);
                this._deviceDiscoveryTimer = null;
                return;
            } else if (this._deviceDiscoveryAttempts < this.config.deviceDiscoveryRetries) {
                // Retry if we haven't reached max attempts
                this._deviceDiscoveryAttempts++;
                console.log(`Device discovery attempt ${this._deviceDiscoveryAttempts}/${this.config.deviceDiscoveryRetries} - no devices found, retrying in ${this.config.deviceDiscoveryDelay}ms`);
                
                this._deviceDiscoveryTimer = setTimeout(() => {
                    this._attemptDeviceDiscovery();
                }, this.config.deviceDiscoveryDelay);
            } else {
                // Max retries reached
                console.warn(`Device discovery failed after ${this.config.deviceDiscoveryRetries} attempts`);
                this._deviceDiscoveryTimer = null;
                // Still update the list with empty array to show "No devices found"
                this.updateDeviceList([]);
            }
        } catch (e) {
            console.warn(`Device discovery attempt ${this._deviceDiscoveryAttempts + 1} failed:`, e);
            
            if (this._deviceDiscoveryAttempts < this.config.deviceDiscoveryRetries) {
                this._deviceDiscoveryAttempts++;
                this._deviceDiscoveryTimer = setTimeout(() => {
                    this._attemptDeviceDiscovery();
                }, this.config.deviceDiscoveryDelay);
            } else {
                console.warn(`Device discovery failed after ${this.config.deviceDiscoveryRetries} attempts`);
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

        el.innerHTML = `
            <div class="telemetry-item" data-sensor-key="SYS_Power">
                <span class="telemetry-label">SYS</span>
                <span class="telemetry-value">${(this.sensorData.SYS_Power || 0).toFixed(1)} W</span>
            </div>
            <div class="telemetry-item" data-sensor-key="CPU_Power">
                <span class="telemetry-label">CPU</span>
                <span class="telemetry-value">${(this.sensorData.CPU_Power || 0).toFixed(1)} W</span>
            </div>
            <div class="telemetry-item" data-sensor-key="GPU_Power">
                <span class="telemetry-label">GPU</span>
                <span class="telemetry-value">${(this.sensorData.GPU_Power || 0).toFixed(1)} W</span>
            </div>
            <div class="telemetry-item" data-sensor-key="MB_Power">
                <span class="telemetry-label">MB</span>
                <span class="telemetry-value">${(this.sensorData.MB_Power || 0).toFixed(1)} W</span>
            </div>
        `;
    }

    renderTemperaturesCard() {
        const el = document.getElementById('card-temperatures');
        if (!el) return;

        el.innerHTML = `
            <div class="telemetry-item" data-sensor-key="Chip_Temp">
                <span class="telemetry-label">Chip</span>
                <span class="telemetry-value">${this.sensorData.Chip_Temp || 0}°C</span>
            </div>
            <div class="telemetry-item" data-sensor-key="Ambient_Temp">
                <span class="telemetry-label">Ambient</span>
                <span class="telemetry-value">${this.sensorData.Ambient_Temp || 0}°C</span>
            </div>
            <div class="telemetry-item" data-sensor-key="Humidity">
                <span class="telemetry-label">Humidity</span>
                <span class="telemetry-value">${(this.sensorData.Humidity || 0).toFixed(1)}%</span>
            </div>
            <div class="telemetry-item" data-sensor-key="Temp_Sensor_1">
                <span class="telemetry-label">S1</span>
                <span class="telemetry-value">${this.sensorData.Temp_Sensor_1 || 'N/A'}</span>
            </div>
            <div class="telemetry-item" data-sensor-key="Temp_Sensor_2">
                <span class="telemetry-label">S2</span>
                <span class="telemetry-value">${this.sensorData.Temp_Sensor_2 || 'N/A'}</span>
            </div>
            <div class="telemetry-item" data-sensor-key="Temp_Sensor_3">
                <span class="telemetry-label">S3</span>
                <span class="telemetry-value">${this.sensorData.Temp_Sensor_3 || 'N/A'}</span>
            </div>
            <div class="telemetry-item" data-sensor-key="Temp_Sensor_4">
                <span class="telemetry-label">S4</span>
                <span class="telemetry-value">${this.sensorData.Temp_Sensor_4 || 'N/A'}</span>
            </div>
        `;
    }

    renderFansCard() {
        const el = document.getElementById('card-fans');
        if (!el) return;

        let html = '';
        for (let i = 1; i <= 9; i++) {
            const duty = this.sensorData[`Fan${i}_Duty`] || 0;
            const rpm  = this.sensorData[`Fan${i}_RPM`]  || 0;
            // Explicit check: treat numeric 1 as ON, everything else as OFF
            const status = this.sensorData[`Fan${i}_Status`] === 1 ? 'ON' : 'OFF';
            html += `
                <div class="telemetry-item" data-sensor-key="Fan${i}_Duty">
                    <span class="telemetry-label">Fan${i}</span>
                    <span class="telemetry-value">${duty}% | ${rpm} RPM | ${status}</span>
                </div>
            `;
        }
        html += `
            <div class="telemetry-item" data-sensor-key="FanExtDuty">
                <span class="telemetry-label">Ext Fan Duty</span>
                <span class="telemetry-value">${this.sensorData.FanExtDuty || 0}%</span>
            </div>
        `;
        el.innerHTML = html;
    }

    renderPowerCard() {
        const el = document.getElementById('card-power');
        if (!el) return;

        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        let html = '';

        rails.forEach(rail => {
            const value = this.sensorData[`${rail}_Power`] || 0;
            html += `
                <div class="telemetry-item" data-sensor-key="${rail}_Power">
                    <span class="telemetry-label">${rail}</span>
                    <span class="telemetry-value">${value.toFixed(2)} W</span>
                </div>
            `;
        });

        el.innerHTML = html;
    }

    renderCurrentCard() {
        const el = document.getElementById('card-current');
        if (!el) return;

        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        let html = '';

        rails.forEach(rail => {
            const value = this.sensorData[`${rail}_Current`] || 0;
            html += `
                <div class="telemetry-item" data-sensor-key="${rail}_Current">
                    <span class="telemetry-label">${rail}</span>
                    <span class="telemetry-value">${value.toFixed(2)} A</span>
                </div>
            `;
        });

        el.innerHTML = html;
    }

    renderVoltageCard() {
        const el = document.getElementById('card-voltage');
        if (!el) return;

        const rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2'];
        let html = '';

        rails.forEach(rail => {
            const value = this.sensorData[`${rail}_Voltage`] || 0;
            html += `
                <div class="telemetry-item" data-sensor-key="${rail}_Voltage">
                    <span class="telemetry-label">${rail}</span>
                    <span class="telemetry-value">${value.toFixed(2)} V</span>
                </div>
            `;
        });

        el.innerHTML = html;
    }

    renderVinsCard() {
        const el = document.getElementById('card-vins');
        if (!el) return;

        let html = '';
        for (let i = 0; i <= 12; i++) {
            const value = this.sensorData[`VIN_${i}`] || 0;
            html += `
                <div class="telemetry-item" data-sensor-key="VIN_${i}">
                    <span class="telemetry-label">VIN_${i}</span>
                    <span class="telemetry-value">${value.toFixed(3)} V</span>
                </div>
            `;
        }
        html += `
            <div class="telemetry-item" data-sensor-key="Vdd">
                <span class="telemetry-label">Vdd</span>
                <span class="telemetry-value">${(this.sensorData.Vdd || 0).toFixed(3)} V</span>
            </div>
            <div class="telemetry-item" data-sensor-key="Vref">
                <span class="telemetry-label">Vref</span>
                <span class="telemetry-value">${(this.sensorData.Vref || 0).toFixed(3)} V</span>
            </div>
        `;
        el.innerHTML = html;
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