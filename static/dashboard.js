'use strict';

let snapshot = null;
const byId = id => document.getElementById(id);
const issue = device => !['ok', 'reachable'].includes(device.status);
const statusNames = {ok: 'Telemetry OK', warning: 'Warning', reachable: 'TCP reachable', unreachable: 'TCP unreachable', dns_error: 'DNS error', metrics_error: 'WinRM / query error', collection_error: 'Collection error'};

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function metric(value, unit = '%') {
    return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)}${unit}` : '—';
}

function average(devices, key) {
    const values = devices.map(d => d[key]).filter(v => typeof v === 'number' && Number.isFinite(v));
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function renderDevices() {
    if (!snapshot) return;
    const term = byId('search').value.toLowerCase();
    const site = byId('siteFilter').value;
    const filtered = snapshot.devices.filter(d => (!site || site === d.site) &&
        (!byId('issuesOnly').checked || issue(d)) &&
        `${d.name} ${d.hostname} ${d.ip} ${d.type}`.toLowerCase().includes(term));
    const table = byId('deviceTable');
    table.replaceChildren();
    if (!filtered.length) {
        const cell = element('td', 'No matching devices. Check discovery messages if the inventory is empty.');
        cell.colSpan = 10;
        const emptyRow = element('tr');
        emptyRow.append(cell);
        table.append(emptyRow);
    }
    const details = byId('hostDetails');
    details.replaceChildren();
    for (const d of filtered) {
        const row = element('tr');
        const name = element('td');
        name.append(element('div', d.name, 'device-name'), element('small', d.hostname || 'IP endpoint', 'muted'));
        row.append(name, element('td', d.site), element('td', d.type), element('td', d.ip || 'Unresolved'));
        row.append(element('td', statusNames[d.status] || d.status, issue(d) ? 'offline' : 'online'));
        for (const key of ['cpu', 'ram', 'disk']) row.append(element('td', metric(d[key])));
        row.append(element('td', metric(d.uptime, ' h')));
        row.append(element('td', d.error || (d.alerts || []).join('; ') || (d.kind === 'tcp' ? `TCP ${d.port}; endpoint only` : 'Host query completed')));
        table.append(row);
        if (d.kind !== 'windows') continue;
        const card = element('details', undefined, 'info-card');
        card.append(element('summary', d.name));
        const list = element('ul');
        for (const service of d.services || []) list.append(element('li', `${service.Name}: ${service.Status}`));
        for (const disk of d.disks || []) list.append(element('li', `${disk.Name} ${disk.Label || ''}: ${metric(disk.UsedPercent)} used · ${disk.FreeGB} GB free`));
        if (d.cluster) {
            list.append(element('li', `Cluster: ${d.cluster.Name}`));
            for (const node of d.cluster.Nodes || []) list.append(element('li', `${node.Name}: ${node.State}`));
            for (const group of d.cluster.Groups || []) list.append(element('li', `${group.Name}: ${group.State} · owner ${group.OwnerNode}`));
        }
        if (d.replication) list.append(element('li', `AD replication failure records: ${d.replication.FailureCount}`));
        for (const [check, state] of Object.entries(d.checks || {})) {
            if (state !== 'ok') list.append(element('li', `${check}: unavailable / permission required`));
        }
        if (!list.children.length) list.append(element('li', d.error || 'No detailed telemetry available'));
        card.append(list);
        details.append(card);
    }
}

function render(data) {
    snapshot = data;
    const devices = data.devices;
    byId('domain').textContent = data.domain;
    byId('interval').textContent = `${data.refresh_seconds} sec after cycle`;
    byId('total').textContent = devices.length;
    byId('monitored').textContent = devices.filter(d => ['ok', 'warning'].includes(d.status) && d.kind === 'windows').length;
    byId('reachable').textContent = devices.filter(d => d.online).length;
    byId('attention').textContent = devices.filter(issue).length;
    const sites = [...new Set(devices.map(d => d.site))].sort();
    byId('sites').textContent = sites.filter(s => s !== 'Unmapped').length;
    const selection = byId('siteFilter').value;
    byId('siteFilter').replaceChildren(new Option('All sites', ''));
    for (const site of sites) byId('siteFilter').append(new Option(site, site));
    if (sites.includes(selection)) byId('siteFilter').value = selection;
    for (const key of ['cpu', 'ram', 'disk']) byId(key).textContent = metric(average(devices, key));
    const messages = [...data.errors];
    if (data.stale) messages.unshift(data.updated_at ? 'Data is stale; last known readings remain visible.' : 'Waiting for the first completed collection cycle.');
    byId('messages').hidden = !messages.length;
    byId('messages').textContent = messages.join(' · ');
    const state = data.errors.length ? 'Discovery / collector issue' : data.stale ? 'Waiting / stale' : data.collecting ? 'Collecting' : 'Cycle complete';
    byId('collectorState').textContent = state;
    byId('liveState').textContent = state;
    byId('liveState').classList.toggle('degraded', Boolean(data.errors.length || data.stale));
    byId('lastUpdate').textContent = data.updated_at ? `Collected ${new Date(data.updated_at).toLocaleString()} · cycle ${data.duration_seconds}s` : 'No collection yet';
    byId('scope').textContent = `Domain: ${data.domain} · Discovery DC: ${data.discovery_dc} · ` + data.networks.map(n => `${n.site}: ${n.prefix}`).join(' · ');
    renderDevices();
}

async function poll() {
    try {
        const response = await fetch('/api/status', {cache: 'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        render(await response.json());
    } catch (_) {
        byId('messages').hidden = false;
        byId('messages').textContent = 'Dashboard API unavailable. Existing readings are not current.';
        byId('liveState').textContent = 'API unavailable';
        byId('liveState').classList.add('degraded');
    } finally {
        window.setTimeout(poll, 5000);
    }
}

for (const id of ['search', 'siteFilter', 'issuesOnly']) byId(id).addEventListener('input', renderDevices);
poll();
