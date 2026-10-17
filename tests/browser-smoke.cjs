// Run against the API test harness; fixture data is never used by the application.
// Requires Playwright installed in the test environment (no frontend runtime dependency).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');

(async () => {
    const browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let fixture = null;
    let unavailable = false;
    await page.route('**/api/status', async route => {
        if (unavailable) return route.abort();
        if (!fixture) return route.continue();
        return route.fulfill({json: fixture});
    });
    const url = process.env.MONITOR_TEST_URL || 'http://127.0.0.1:8765';
    try {
        for (let attempt = 0; attempt < 30; attempt++) {
            try { if ((await fetch(`${url}/health`)).ok) break; } catch {}
            await new Promise(resolve => setTimeout(resolve, 200));
        }
        await page.goto(url);
        await page.waitForFunction(() => document.getElementById('total').textContent === '0');
        assert.equal(await page.locator('#cpu').textContent(), '—');
        assert.match(await page.locator('#liveState').textContent(), /Waiting/);
        fixture = {
            devices: [
                {name: '<img src=x onerror=alert(1)>', hostname: 'test.example.test', ip: '10.10.10.10', site: 'SITE-A', type: 'Domain Controller', kind: 'windows', status: 'ok', online: true, cpu: 0, ram: 50, disk: 40, uptime: 1, services: [], disks: [], alerts: [], checks: {}},
                {name: 'FS-NODE1', hostname: 'tw-cl1.example.test', ip: '10.20.40.11', site: 'SITE-B', type: 'Cluster Node', kind: 'windows', status: 'metrics_error', online: true, cpu: null, ram: null, disk: null, uptime: null, error: 'Access denied', alerts: []},
                {name: 'FILES', ip: '10.20.40.20', site: 'SITE-B', type: 'SMB endpoint', kind: 'tcp', port: 445, status: 'reachable', online: true, cpu: null, ram: null, disk: null, uptime: null, alerts: []}
            ],
            errors: [], updated_at: new Date().toISOString(), duration_seconds: 1,
            stale: false, collecting: false, domain: 'example.test', discovery_dc: 'test.example.test', refresh_seconds: 30,
            networks: [{site: 'SITE-A', prefix: '10.10.10.0/24'}, {site: 'SITE-B', prefix: '10.20.40.0/24'}]
        };
        await page.reload();
        await page.waitForFunction(() => document.getElementById('total').textContent === '3');
        assert.equal(await page.locator('#deviceTable img').count(), 0, 'AD text must never become HTML');
        assert.equal(await page.locator('#cpu').textContent(), '0.0%');
        assert.equal(await page.locator('#attention').textContent(), '1');
        await page.selectOption('#siteFilter', 'SITE-B');
        assert.equal(await page.locator('#deviceTable tr').count(), 2);
        await page.check('#issuesOnly');
        assert.equal(await page.locator('#deviceTable tr').count(), 1);
        assert.match(await page.locator('#deviceTable').textContent(), /Access denied/);
        assert.equal(await page.locator('#deviceTable td').nth(5).textContent(), '—');
        await page.fill('#search', 'no-such-host');
        assert.match(await page.locator('#deviceTable').textContent(), /No matching devices/);
        await page.fill('#search', '');
        await page.uncheck('#issuesOnly');
        await page.setViewportSize({width: 390, height: 844});
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Mobile document should not overflow');
        unavailable = true;
        await page.reload();
        await page.waitForFunction(() => document.getElementById('liveState').textContent === 'API unavailable');
        assert.deepEqual(errors, []);
        console.log('Browser smoke passed: empty state, safe text, real zero/null metrics, site/attention/search filters, mobile layout, API failure.');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
