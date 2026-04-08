/**
 * Xeneon Dashboard JavaScript
 * Real-time telemetry updates and UI interactions
 */

class XeneonDashboard {
    constructor() {
        this.ws = null;
        this.apiBase = '/api';
        this.activeDevice = null;
        this.connectionStatus = 'disconnected';
        this.lastUpdate = null;
        this.uptimeStart = null;
        
        // Configuration
        this.config = {
            refreshInterval: 1000, // 1 second
            reconnectDelay: 3000,
            maxHistory: 100
        };
        
        // Initialize dashboard
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.setupTabs();
        this.connectToWebSocket();
        this.startPolling();
        
        // Initial data fetch
        this.refreshFleet();
    }
    
    setupEventListeners() {
        // Tab switching
        document.querySelectorAll('.tab-button').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.switchTab(e.target.closest('.tab-button').dataset.tab);
            });
        });
        
        // Refresh fleet button
        const refreshBtn = document.getElementById('refresh-fleet');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => this.refreshFleet());
        }
        
        // Window resize for responsive design
        window.addEventListener('resize', () => this.handleResize());
    }
    
    setupTabs() {
        // Set initial active tab
        const urlParams = new URLSearchParams(window.location.search);
        const tab = urlParams.get('tab') || 'fleet';
        this.switchTab(tab);
    }
    
    switchTab(tabId) {
        // Update URL without reloading
        const url = new URL(window.location);
        url.searchParams.set('tab', tabId);
        window.history.replaceState({}, '', url);
        
        // Update tab buttons
        document.querySelectorAll('.tab-button').forEach(btn => {
            if (btn.dataset.tab === tabId) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
        
        // Update content
        document.querySelectorAll('.tab-content').forEach(content => {
            if (content.id === `${tabId}-tab`) {
                content.classList.add('active');
            } else {
                content.classList.remove('active');
            }
        });
        
        // Refresh data for the active tab
        this.refreshTabData(tabId);
    }
    
    refreshTabData(tabId) {
        switch (tabId) {
            case 'fleet':
                this.refreshFleet();
                break;
            case 'device':
                this.refreshDevice();
                break;
            case 'system':
                this.refreshSystem();
                break;
            case 'voltage':
                this.refreshVoltage();
                break;
            case 'temperature':
                this.refreshTemperature();
                break;
            case 'fans':
                this.refreshFans();
                break;
        }
    }
    
    connectToWebSocket() {
        try {
            // Try to connect to WebSocket
            const wsUrl = this.getWebSocketUrl();
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                console.log('WebSocket connected');
                this.setConnectionStatus('connected');
                this.subscribeToTelemetry();
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleTelemetryUpdate(data);
                } catch (e) {
                    console.error('Error parsing WebSocket message:', e);
                }
            };
            
            this.ws.onclose = () => {
                console.log('WebSocket disconnected');
                this.setConnectionStatus('disconnected');
                this.scheduleReconnect();
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.setConnectionStatus('error');
            };
            
        } catch (error) {
            console.error('Failed to create WebSocket:', error);
            this.setConnectionStatus('disconnected');
        }
    }
    
    getWebSocketUrl() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        return `${protocol}//${host}/api/device/${this.activeDevice || 'all'}/stream`;
    }
    
    subscribeToTelemetry() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            // Subscribe to all device telemetry
            this.ws.send(JSON.stringify({ action: 'subscribe', device: 'all' }));
        }
    }
    
    handleTelemetryUpdate(data) {
        this.lastUpdate = new Date();
        this.updateLastUpdateTime();
        
        // Update all tabs with new data
        this.updateFleetStatus(data);
        this.updateDeviceData(data);
        this.updateSystemData(data);
        this.updateVoltageData(data);
        this.updateTemperatureData(data);
        this.updateFansData(data);
    }
    
    startPolling() {
        setInterval(() => {
            if (this.connectionStatus !== 'connected') {
                // Fallback to REST polling when WebSocket is not available
                this.refreshFleet();
                if (this.activeDevice) {
                    this.refreshDevice();
                    this.refreshSystem();
                    this.refreshVoltage();
                    this.refreshTemperature();
                    this.refreshFans();
                }
            }
        }, this.config.refreshInterval);
    }
    
    scheduleReconnect() {
        setTimeout(() => {
            if (this.connectionStatus !== 'connected') {
                this.connectToWebSocket();
            }
        }, this.config.reconnectDelay);
    }
    
    // API Methods
    async apiGet(endpoint) {
        try {
            const response = await fetch(`${this.apiBase}${endpoint}`);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            return await response.json();
        } catch (error) {
            console.error(`API error for ${endpoint}:`, error);
            throw error;
        }
    }
    
    async apiPost(endpoint, data) {
        try {
            const response = await fetch(`${this.apiBase}${endpoint}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data)
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            return await response.json();
        } catch (error) {
            console.error(`API error for ${endpoint}:`, error);
            throw error;
        }
    }
    
    // Data Refresh Methods
    async refreshFleet() {
        try {
            // Use POST /scan to trigger a device rescan
            const scanResult = await this.apiPost('/scan', {});
            const devices = scanResult.devices || [];
            
            // Show scan results
            if (scanResult.new_devices && scanResult.new_devices.length > 0) {
                console.log('New devices found:', scanResult.new_devices);
            }
            if (scanResult.disconnected_devices && scanResult.disconnected_devices.length > 0) {
                console.log('Devices disconnected:', scanResult.disconnected_devices);
            }
            
            this.updateDeviceList(devices);
            
            // If no active device, try to connect to first available
            if (!this.activeDevice && devices.length > 0) {
                this.activeDevice = devices[0].uid;
                // Try WebSocket but don't fail if it doesn't work
                this.connectToWebSocket();
            }
            
            // If we have devices and got a successful response, mark as connected
            if (devices.length > 0 && this.connectionStatus === 'disconnected') {
                // We're using REST polling, which is working
                this.setConnectionStatus('connected');
            }
            
        } catch (error) {
            console.error('Error refreshing fleet:', error);
            // Fallback to GET /devices if scan fails
            try {
                const devices = await this.apiGet('/devices');
                this.updateDeviceList(devices);
                
                // If we got devices via fallback, we're connected via REST
                if (devices.length > 0 && this.connectionStatus === 'disconnected') {
                    this.setConnectionStatus('connected');
                }
            } catch (fallbackError) {
                console.error('Fallback also failed:', fallbackError);
                this.showErrorMessage('Failed to refresh fleet data');
            }
        }
    }
    
    async refreshDevice() {
        if (!this.activeDevice) return;
        
        try {
            const info = await this.apiGet(`/device/${this.activeDevice}/info`);
            this.updateDeviceInfo(info);
        } catch (error) {
            console.error('Error refreshing device info:', error);
        }
    }
    
    async refreshSystem() {
        if (!this.activeDevice) return;
        
        try {
            const telemetry = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
            this.updateSystemTelemetry(telemetry);
        } catch (error) {
            console.error('Error refreshing system telemetry:', error);
        }
    }
    
    async refreshVoltage() {
        if (!this.activeDevice) return;
        
        try {
            const telemetry = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
            this.updateVoltageTelemetry(telemetry);
        } catch (error) {
            console.error('Error refreshing voltage telemetry:', error);
        }
    }
    
    async refreshTemperature() {
        if (!this.activeDevice) return;
        
        try {
            const telemetry = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
            this.updateTemperatureTelemetry(telemetry);
        } catch (error) {
            console.error('Error refreshing temperature telemetry:', error);
        }
    }
    
    async refreshFans() {
        if (!this.activeDevice) return;
        
        try {
            const telemetry = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
            this.updateFansTelemetry(telemetry);
        } catch (error) {
            console.error('Error refreshing fans telemetry:', error);
        }
    }
    
    // UI Update Methods
    setConnectionStatus(status) {
        this.connectionStatus = status;
        const statusIndicator = document.getElementById('connection-status');
        const statusText = document.getElementById('status-text');
        const connectionState = document.getElementById('connection-state');
        
        if (statusIndicator && statusText && connectionState) {
            switch (status) {
                case 'connected':
                    statusIndicator.className = 'status-indicator status-connected';
                    statusText.textContent = 'Connected';
                    connectionState.textContent = 'Connected';
                    connectionState.className = 'status-value status-connected';
                    if (!this.uptimeStart) {
                        this.uptimeStart = new Date();
                    }
                    break;
                case 'disconnected':
                    statusIndicator.className = 'status-indicator status-disconnected';
                    statusText.textContent = 'Disconnected';
                    connectionState.textContent = 'Disconnected';
                    connectionState.className = 'status-value status-disconnected';
                    this.uptimeStart = null;
                    break;
                case 'error':
                    statusIndicator.className = 'status-indicator status-danger';
                    statusText.textContent = 'Connection Error';
                    connectionState.textContent = 'Error';
                    connectionState.className = 'status-value status-danger';
                    break;
            }
        }
    }
    
    updateLastUpdateTime() {
        if (this.lastUpdate && document.getElementById('last-update')) {
            const timeStr = this.lastUpdate.toLocaleTimeString();
            document.getElementById('last-update').textContent = `Last update: ${timeStr}`;
        }
    }
    
    updateDeviceList(devices) {
        const deviceList = document.getElementById('device-list');
        const activeDeviceEl = document.getElementById('active-device');
        
        if (!deviceList) return;
        
        if (devices.length === 0) {
            deviceList.innerHTML = `
                <div class="device-item placeholder">
                    <div class="device-info">
                        <span class="device-name">No devices found</span>
                        <span class="device-details">Ensure devices are connected and powered</span>
                    </div>
                </div>
            `;
            if (activeDeviceEl) activeDeviceEl.textContent = 'None';
            return;
        }
        
        deviceList.innerHTML = '';
        devices.forEach(device => {
            const isActive = this.activeDevice === device.uid;
            const deviceItem = document.createElement('div');
            deviceItem.className = `device-item ${isActive ? 'active' : ''}`;
            
            deviceItem.innerHTML = `
                <div class="device-info">
                    <span class="device-name">${device.uid}</span>
                    <span class="device-details">Port: ${device.port}</span>
                </div>
                <div class="device-status ${this.connectionStatus === 'connected' ? 'connected' : 'disconnected'}">
                    ${this.connectionStatus === 'connected' ? 'Connected' : 'Disconnected'}
                </div>
            `;
            
            // Add click event to select device
            deviceItem.addEventListener('click', () => {
                this.activeDevice = device.uid;
                this.connectToWebSocket();
                this.refreshTabData(document.querySelector('.tab-button.active').dataset.tab);
            });
            
            deviceList.appendChild(deviceItem);
        });
        
        if (activeDeviceEl) {
            activeDeviceEl.textContent = this.activeDevice || 'None';
        }
    }
    
    updateDeviceInfo(info) {
        const portEl = document.getElementById('device-port');
        const uidEl = document.getElementById('device-uid');
        const fwEl = document.getElementById('device-fw');
        const fanSwitchEl = document.getElementById('fan-switch');
        const rgbSwitchEl = document.getElementById('rgb-switch');
        const rgbExtEl = document.getElementById('rgb-ext');
        
        if (portEl) portEl.textContent = info.port || '-';
        if (uidEl) uidEl.textContent = info.UID || info.uid || '-';
        if (fwEl) fwEl.textContent = `0x${info.FwVersion?.toString(16).toUpperCase() || '00'}`;
        if (fanSwitchEl) fanSwitchEl.textContent = info.FanSwitchStatus || '-';
        if (rgbSwitchEl) rgbSwitchEl.textContent = info.RGBSwitchStatus || '-';
        if (rgbExtEl) rgbExtEl.textContent = info.RGBExtStatus || '-';
    }
    
    updateSystemTelemetry(telemetry) {
        // Summary section
        const summaryData = [
            { key: 'SYS_Power', label: 'SYS Power', unit: 'W' },
            { key: 'CPU_Power', label: 'CPU Power', unit: 'W' },
            { key: 'GPU_Power', label: 'GPU Power', unit: 'W' },
            { key: 'MB_Power', label: 'MB Power', unit: 'W' }
        ];
        
        this.updateTelemetryList('summary-telemetry', summaryData, telemetry);
        
        // Power telemetry for rails
        const powerData = [
            { key: 'EPS1_Power', label: 'EPS_1', unit: 'W' },
            { key: 'EPS2_Power', label: 'EPS_2', unit: 'W' },
            { key: 'PCIE8_1_Power', label: 'PCIE8_1', unit: 'W' },
            { key: 'PCIE8_2_Power', label: 'PCIE8_2', unit: 'W' },
            { key: 'PCIE8_3_Power', label: 'PCIE8_3', unit: 'W' },
            { key: 'HPWR1_Power', label: '12V_HPWR_1', unit: 'W' },
            { key: 'HPWR2_Power', label: '12V_HPWR_2', unit: 'W' }
        ];
        
        this.updateTelemetryList('power-telemetry', powerData, telemetry);
        
        // Current telemetry
        const currentData = [
            { key: 'EPS1_Current', label: 'EPS_1', unit: 'A' },
            { key: 'EPS2_Current', label: 'EPS_2', unit: 'A' },
            { key: 'PCIE8_1_Current', label: 'PCIE8_1', unit: 'A' },
            { key: 'PCIE8_2_Current', label: 'PCIE8_2', unit: 'A' },
            { key: 'PCIE8_3_Current', label: 'PCIE8_3', unit: 'A' },
            { key: 'HPWR1_Current', label: '12V_HPWR_1', unit: 'A' },
            { key: 'HPWR2_Current', label: '12V_HPWR_2', unit: 'A' }
        ];
        
        this.updateTelemetryList('current-telemetry', currentData, telemetry);
        
        // Voltage telemetry
        const voltageData = [
            { key: 'EPS1_Voltage', label: 'EPS_1', unit: 'V' },
            { key: 'EPS2_Voltage', label: 'EPS_2', unit: 'V' },
            { key: 'PCIE8_1_Voltage', label: 'PCIE8_1', unit: 'V' },
            { key: 'PCIE8_2_Voltage', label: 'PCIE8_2', unit: 'V' },
            { key: 'PCIE8_3_Voltage', label: 'PCIE8_3', unit: 'V' },
            { key: 'HPWR1_Voltage', label: '12V_HPWR_1', unit: 'V' },
            { key: 'HPWR2_Voltage', label: '12V_HPWR_2', unit: 'V' }
        ];
        
        this.updateTelemetryList('voltage-telemetry', voltageData, telemetry);
    }
    
    updateVoltageTelemetry(telemetry) {
        // Board voltages
        const boardData = [
            { key: 'Vdd', label: 'Vdd', unit: 'V', max: 5.0 },
            { key: 'Vref', label: 'Vref', unit: 'V', max: 5.0 }
        ];
        
        this.updateVoltageList('board-voltages', boardData, telemetry);
        
        // VIN measurements
        const vinData = [];
        for (let i = 0; i < 13; i++) {
            vinData.push({ key: `VIN_${i}`, label: `VIN_${i}`, unit: 'V', max: 15.0 });
        }
        
        this.updateVoltageList('vin-voltages', vinData, telemetry);
    }
    
    updateTemperatureTelemetry(telemetry) {
        // Chip temperature
        const chipTemp = telemetry.Chip_Temp || 0;
        const chipTempEl = document.getElementById('chip-temp');
        const chipTempBar = document.getElementById('chip-temp-bar');
        
        if (chipTempEl) {
            chipTempEl.querySelector('.temp-value').textContent = `${chipTemp.toFixed(1)} °C`;
        }
        
        if (chipTempBar) {
            const percentage = Math.min(100, (chipTemp / 100) * 100);
            chipTempBar.style.width = `${percentage}%`;
            
            // Color coding based on temperature
            if (chipTemp > 70) {
                chipTempBar.className = 'temp-bar danger';
            } else if (chipTemp > 50) {
                chipTempBar.className = 'temp-bar warning';
            } else {
                chipTempBar.className = 'temp-bar';
            }
        }
        
        // Ambient temperature and humidity
        const ambientTempEl = document.getElementById('ambient-temp');
        const humidityEl = document.getElementById('humidity');
        
        if (ambientTempEl) {
            ambientTempEl.textContent = `${(telemetry.Ambient_Temp || 0).toFixed(1)} °C`;
        }
        
        if (humidityEl) {
            humidityEl.textContent = `${(telemetry.Humidity || 0).toFixed(1)} %`;
        }
        
        // Sensor temperatures
        const sensorData = [];
        for (let i = 1; i <= 4; i++) {
            sensorData.push({ key: `Temp_Sensor_${i}`, label: `Sensor ${i}`, unit: '°C' });
        }
        
        this.updateSensorList('sensor-temps', sensorData, telemetry);
    }
    
    updateFansTelemetry(telemetry) {
        // Fan data (assuming we get fan telemetry)
        const fanData = [];
        for (let i = 1; i <= 8; i++) {  // Assuming up to 8 fans
            fanData.push({
                name: `Fan ${i}`,
                rpm: telemetry[`Fan${i}_RPM`] || 0,
                duty: telemetry[`Fan${i}_Duty`] || 0,
                enabled: telemetry[`Fan${i}_Enable`] !== false
            });
        }
        
        this.updateFanList('fan-list', fanData);
        
        // External fan
        const extFanDuty = telemetry.FanExtDuty || 0;
        const extFanDutyEl = document.getElementById('ext-fan-duty');
        const extFanBar = document.getElementById('ext-fan-bar');
        
        if (extFanDutyEl) {
            extFanDutyEl.textContent = `${extFanDuty}%`;
        }
        
        if (extFanBar) {
            extFanBar.style.width = `${extFanDuty}%`;
        }
    }
    
    // Helper Methods for UI Updates
    updateTelemetryList(containerId, data, telemetry) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        container.innerHTML = '';
        data.forEach(item => {
            const value = telemetry[item.key] || 0;
            const itemEl = document.createElement('div');
            itemEl.className = 'telemetry-item';
            itemEl.innerHTML = `
                <span class="telemetry-label">${item.label}</span>
                <span class="telemetry-value">${value.toFixed(2)} ${item.unit}</span>
            `;
            container.appendChild(itemEl);
        });
    }
    
    updateVoltageList(containerId, data, telemetry) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        container.innerHTML = '';
        data.forEach(item => {
            const value = telemetry[item.key] || 0;
            const percentage = item.max ? Math.min(100, (value / item.max) * 100) : 0;
            
            const itemEl = document.createElement('div');
            itemEl.className = 'voltage-item';
            itemEl.innerHTML = `
                <span class="voltage-label">${item.label}</span>
                <span class="voltage-value">${value.toFixed(3)} ${item.unit}</span>
            `;
            container.appendChild(itemEl);
        });
    }
    
    updateSensorList(containerId, data, telemetry) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        container.innerHTML = '';
        data.forEach(item => {
            const value = telemetry[item.key] || 0;
            const itemEl = document.createElement('div');
            itemEl.className = 'sensor-item';
            itemEl.innerHTML = `
                <span class="sensor-label">${item.label}</span>
                <span class="sensor-value">${value.toFixed(1)} °C</span>
            `;
            container.appendChild(itemEl);
        });
    }
    
    updateFanList(containerId, data) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        container.innerHTML = '';
        data.forEach(fan => {
            const rpmPercentage = Math.min(100, (fan.rpm / 3000) * 100);
            
            const itemEl = document.createElement('div');
            itemEl.className = 'fan-item';
            itemEl.innerHTML = `
                <span class="fan-name">${fan.name}</span>
                <span class="fan-value">${fan.duty}%</span>
                <span class="fan-value">${fan.rpm} RPM</span>
                <span class="fan-value">${fan.enabled ? 'Yes' : 'No'}</span>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${rpmPercentage}%"></div>
                </div>
            `;
            container.appendChild(itemEl);
        });
    }
    
    updateFleetStatus(data) {
        // Update uptime display
        if (this.uptimeStart) {
            const uptime = new Date() - this.uptimeStart;
            const uptimeStr = this.formatUptime(uptime);
            const uptimeEl = document.getElementById('uptime');
            if (uptimeEl) {
                uptimeEl.textContent = uptimeStr;
            }
        }
    }
    
    formatUptime(ms) {
        const seconds = Math.floor(ms / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);
        
        const h = hours % 24;
        const m = minutes % 60;
        const s = seconds % 60;
        
        return `${days}d ${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    
    handleResize() {
        // Handle responsive layout changes
        const width = window.innerWidth;
        if (width <= 768) {
            // Mobile layout adjustments
            document.body.style.fontSize = '12px';
        } else if (width <= 480) {
            // Small mobile adjustments
            document.body.style.fontSize = '11px';
        } else {
            document.body.style.fontSize = '14px';
        }
    }
    
    showErrorMessage(message) {
        // Create temporary error message
        const errorEl = document.createElement('div');
        errorEl.className = 'error-message';
        errorEl.textContent = message;
        errorEl.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: var(--color-danger);
            color: white;
            padding: 10px 20px;
            border-radius: 4px;
            z-index: 1000;
            box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        `;
        
        document.body.appendChild(errorEl);
        
        setTimeout(() => {
            errorEl.remove();
        }, 3000);
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.xeneonDashboard = new XeneonDashboard();
});

// Export for external use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = XeneonDashboard;
}