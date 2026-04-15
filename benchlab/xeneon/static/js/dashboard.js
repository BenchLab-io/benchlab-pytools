/**
 * Xeneon Dashboard JavaScript (HMI Touch Upgrade)
 * - Touch-first interaction model
 * - Swipe navigation
 * - Pointer event unified input
 * - Optimized for 2560x720 kiosk + iframe embed
 */

class XeneonDashboard {
    constructor() {
        this.ws = null;
        this.apiBase = '/api';
        this.activeDevice = null;
        this.connectionStatus = 'disconnected';
        this.lastUpdate = null;
        this.uptimeStart = null;

        this.config = {
            refreshInterval: 1000,
            reconnectDelay: 3000,
            maxHistory: 100
        };

        this.init();
    }

    init() {
        this.setupEventListeners();
        this.setupTabs();
        this.connectToWebSocket();
        this.startPolling();
        this.refreshFleet();
    }

    // =========================================================
    // HMI INPUT LAYER (TOUCH + MOUSE + PEN)
    // =========================================================

    setupEventListeners() {
        this.setupTabButtons();
        this.setupTouchGestures();

        const refreshBtn = document.getElementById('refresh-fleet');
        if (refreshBtn) {
            refreshBtn.addEventListener('pointerdown', (e) => {
                e.preventDefault();
                this.scanForDevices();
            });
        }

        window.addEventListener('resize', () => this.handleResize());
    }

    setupTabButtons() {
        document.querySelectorAll('.tab-button').forEach(btn => {

            const activate = (e) => {
                e.preventDefault();
                e.stopPropagation();

                const tab = btn.dataset.tab;
                this.switchTab(tab);

                // HMI press feedback
                btn.classList.add('pressed');
                setTimeout(() => btn.classList.remove('pressed'), 120);
            };

            // Primary input
            btn.addEventListener('pointerdown', activate);

            // fallback
            btn.addEventListener('click', activate);
        });
    }

    setupTabs() {
        const urlParams = new URLSearchParams(window.location.search);
        const tab = urlParams.get('tab') || 'fleet';
        this.switchTab(tab);
    }

    setupTouchGestures() {
        let startX = 0;
        let startY = 0;
        let tracking = false;

        document.addEventListener('pointerdown', (e) => {
            tracking = true;
            startX = e.clientX;
            startY = e.clientY;
        });

        document.addEventListener('pointerup', (e) => {
            if (!tracking) return;
            tracking = false;

            const dx = e.clientX - startX;
            const dy = e.clientY - startY;

            // ignore vertical gestures
            if (Math.abs(dy) > 80) return;

            // swipe threshold
            if (Math.abs(dx) < 80) return;

            const tabs = this.getTabs();
            if (!tabs.length) return;

            let idx = this.getCurrentTabIndex();

            if (dx < 0) {
                idx = Math.min(idx + 1, tabs.length - 1);
            } else {
                idx = Math.max(idx - 1, 0);
            }

            this.switchTab(tabs[idx]);
        });
    }

    getTabs() {
        return Array.from(document.querySelectorAll('.tab-button'))
            .map(btn => btn.dataset.tab);
    }

    getCurrentTabIndex() {
        const active = document.querySelector('.tab-button.active');
        if (!active) return 0;
        return this.getTabs().indexOf(active.dataset.tab);
    }

    // =========================================================
    // TAB SYSTEM
    // =========================================================

    switchTab(tabId) {
        // HMI transition feedback
        document.body.classList.add('tab-switching');
        setTimeout(() => document.body.classList.remove('tab-switching'), 120);

        // update URL
        const url = new URL(window.location);
        url.searchParams.set('tab', tabId);
        window.history.replaceState({}, '', url);

        // buttons
        document.querySelectorAll('.tab-button').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabId);
        });

        // content
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.toggle('active', content.id === `${tabId}-tab`);
        });

        this.refreshTabData(tabId);
    }

    refreshTabData(tabId) {
        switch (tabId) {
            case 'fleet': this.refreshFleet(); break;
            case 'device': this.refreshDevice(); break;
            case 'system': this.refreshSystem(); break;
            case 'voltage': this.refreshVoltage(); break;
            case 'temperature': this.refreshTemperature(); break;
            case 'fans': this.refreshFans(); break;
        }
    }

    // =========================================================
    // WEBSOCKET
    // =========================================================

    connectToWebSocket() {
        try {
            const wsUrl = this.getWebSocketUrl();
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                this.setConnectionStatus('connected');
                this.subscribeToTelemetry();
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleTelemetryUpdate(data);
                } catch (e) {}
            };

            this.ws.onclose = () => {
                this.setConnectionStatus('disconnected');
                this.scheduleReconnect();
            };

            this.ws.onerror = () => {
                this.setConnectionStatus('error');
            };

        } catch (e) {
            this.setConnectionStatus('disconnected');
        }
    }

    getWebSocketUrl() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${proto}//${location.host}/api/device/${this.activeDevice || 'all'}/stream`;
    }

    subscribeToTelemetry() {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: 'subscribe', device: 'all' }));
        }
    }

    // =========================================================
    // POLLING
    // =========================================================

    startPolling() {
        setInterval(() => {
            this.refreshFleet();

            if (this.activeDevice) {
                this.refreshDevice();
                this.refreshSystem();
                this.refreshVoltage();
                this.refreshTemperature();
                this.refreshFans();
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

    // =========================================================
    // API
    // =========================================================

    async apiGet(endpoint) {
        const res = await fetch(`${this.apiBase}${endpoint}`);
        return await res.json();
    }

    async apiPost(endpoint, data) {
        const res = await fetch(`${this.apiBase}${endpoint}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        return await res.json();
    }

    // =========================================================
    // DATA
    // =========================================================

    async refreshFleet() {
        try {
            const devices = await this.apiGet('/devices');
            const list = Array.isArray(devices) ? devices : (devices.devices || []);

            this.updateDeviceList(list);

            if (!this.activeDevice && list.length > 0) {
                this.activeDevice = list[0].uid;
                this.connectToWebSocket();
            }
        } catch (e) {}
    }

    async scanForDevices() {
        const res = await this.apiPost('/scan', {});
        this.updateDeviceList(res.devices || []);
    }

    async refreshDevice() {
        if (!this.activeDevice) return;
        const info = await this.apiGet(`/device/${this.activeDevice}/info`);
        this.updateDeviceInfo(info);
    }

    async refreshSystem() {
        if (!this.activeDevice) return;
        const t = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
        this.updateSystemTelemetry(t);
    }

    async refreshVoltage() {
        if (!this.activeDevice) return;
        const t = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
        this.updateVoltageTelemetry(t);
    }

    async refreshTemperature() {
        if (!this.activeDevice) return;
        const t = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
        this.updateTemperatureTelemetry(t);
    }

    async refreshFans() {
        if (!this.activeDevice) return;
        const t = await this.apiGet(`/device/${this.activeDevice}/telemetry`);
        this.updateFansTelemetry(t);
    }

    // =========================================================
    // DEVICE LIST
    // =========================================================

    updateDeviceList(devices) {
        const el = document.getElementById('device-list');
        if (!el) return;

        el.innerHTML = '';

        devices.forEach(d => {
            const item = document.createElement('div');
            item.className = 'device-item';

            item.innerHTML = `
                <div class="device-name">${d.uid}</div>
                <div class="device-port">Port: ${d.port}</div>
            `;

            item.addEventListener('pointerdown', () => {
                this.activeDevice = d.uid;
                this.connectToWebSocket();
            });

            el.appendChild(item);
        });
    }

    // =========================================================
    // STATUS
    // =========================================================

    setConnectionStatus(status) {
        this.connectionStatus = status;

        const el = document.getElementById('connection-state');
        if (!el) return;

        el.textContent = status;
    }

    handleResize() {
        const w = window.innerWidth;
        document.body.style.fontSize = w < 800 ? '12px' : '14px';
    }

    handleTelemetryUpdate(data) {}
}

// =========================================================
// INIT
// =========================================================

document.addEventListener('DOMContentLoaded', () => {
    window.xeneonDashboard = new XeneonDashboard();
});