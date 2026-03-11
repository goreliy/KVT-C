/**
 * Archive Server page JavaScript
 */

let isRunning = false;

// Toggle server
async function toggleArchive() {
    const action = isRunning ? 'stop' : 'start';
    
    try {
        await api(`/api/archive/${action}`, 'POST');
        showToast(`Archive сервер ${action === 'start' ? 'запущен' : 'остановлен'}`, 'success');
        loadStatus();
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

// Load status
async function loadStatus() {
    try {
        const data = await api('/api/archive/status');
        isRunning = data.running;
        
        document.getElementById('toggle-text').textContent = isRunning ? '⏹ Stop' : '▶ Start';
        document.getElementById('btn-toggle').className = `btn ${isRunning ? 'btn-danger' : 'btn-success'}`;
        
        // Stats
        document.getElementById('stat-records').textContent = data.data?.total_records || 0;
        document.getElementById('stat-events').textContent = data.events?.total_events || 0;
        document.getElementById('stat-unack').textContent = data.events?.unacknowledged || 0;
        document.getElementById('stat-memory').textContent = (data.data?.memory_usage_mb || 0) + ' MB';
        
        // Files
        if (data.files) {
            const archiveInfo = data.files.archive_exists 
                ? `✓ ${data.files.archive_size_mb} MB` 
                : '✗ не создан';
            const eventsInfo = data.files.events_exists 
                ? `✓ ${data.files.events_size_mb} MB` 
                : '✗ не создан';
            
            document.getElementById('file-archive').textContent = archiveInfo;
            document.getElementById('file-events').textContent = eventsInfo;
            document.getElementById('file-path').textContent = data.files.archive_file?.replace(/[^/\\]*$/, '') || '—';
        }
    } catch (e) {
        console.error('Error loading status:', e);
    }
}

// Save archive now
async function saveNow() {
    try {
        await api('/api/archive/save', 'POST');
        showToast('Архив сохранён в файл', 'success');
        loadStatus();
    } catch (e) {
        showToast('Ошибка сохранения: ' + e.message, 'error');
    }
}

// Load config
async function loadConfig() {
    try {
        const data = await api('/api/archive/config');
        
        document.getElementById('cfg-sensors').value = data.data?.sensor_count || 10;
        document.getElementById('cfg-days').value = data.data?.history_days || 30;
        document.getElementById('cfg-resolution').value = data.data?.data_resolution_ms || 60000;
        document.getElementById('cfg-events').checked = data.events?.include_events !== false;
        document.getElementById('cfg-event-freq').value = (data.events?.event_frequency || 0.01) * 100;
        document.getElementById('event-val').textContent = (data.events?.event_frequency || 0.01).toFixed(2);
        
        showToast('Конфигурация загружена', 'info');
    } catch (e) {
        showToast('Ошибка загрузки: ' + e.message, 'error');
    }
}

// Save config
async function saveConfig() {
    const config = {
        data: {
            sensor_count: parseInt(document.getElementById('cfg-sensors').value),
            history_days: parseInt(document.getElementById('cfg-days').value),
            data_resolution_ms: parseInt(document.getElementById('cfg-resolution').value)
        },
        events: {
            include_events: document.getElementById('cfg-events').checked,
            event_frequency: parseFloat(document.getElementById('cfg-event-freq').value) / 100
        }
    };
    
    try {
        await api('/api/archive/config', 'POST', config);
        showToast('Конфигурация сохранена', 'success');
    } catch (e) {
        showToast('Ошибка сохранения: ' + e.message, 'error');
    }
}

// Regenerate data
async function regenerateData() {
    try {
        await api('/api/archive/regenerate', 'POST');
        showToast('Данные перегенерированы', 'success');
        loadStatus();
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

// Query data
async function queryData() {
    const sensorId = document.getElementById('query-sensor').value;
    const hours = document.getElementById('query-hours').value;
    const resolution = document.getElementById('query-resolution').value;
    
    const toTime = new Date().toISOString();
    const fromTime = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
    
    try {
        const data = await api(`/api/archive/query?sensor_id=${sensorId}&from=${fromTime}&to=${toTime}&resolution=${resolution}`);
        document.getElementById('query-result').textContent = JSON.stringify(data, null, 2);
    } catch (e) {
        document.getElementById('query-result').textContent = 'Ошибка: ' + e.message;
    }
}

// Refresh events
async function refreshEvents() {
    const tbody = document.querySelector('#events-table tbody');
    
    try {
        const data = await api('/api/archive/events?limit=20');
        
        if (!data.events || data.events.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="loading">Нет событий</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.events.map(event => {
            const timestamp = new Date(event.timestamp).toLocaleString('ru-RU');
            const status = event.acknowledged ? '✓' : '⚠️';
            const priorityClass = event.priority === 'high' ? 'alarm' : (event.priority === 'medium' ? 'warning' : '');
            
            return `
                <tr class="status-${priorityClass}">
                    <td>${timestamp}</td>
                    <td>Датчик ${event.sensor_id}</td>
                    <td>${event.event_type}</td>
                    <td>${event.value ?? '—'}</td>
                    <td>${status}</td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading">Ошибка загрузки</td></tr>';
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadStatus();
    loadConfig();
    refreshEvents();
    
    setInterval(loadStatus, 5000);
    setInterval(refreshEvents, 10000);
});
