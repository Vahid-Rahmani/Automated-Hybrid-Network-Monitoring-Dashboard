import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app import create_app
from collector import (CollectionError, collect_one_device, collect_snapshot,
                       discover_ad_computers, merge_inventory, number,
                       remote_script, run_powershell, utc_now)
from config import load_config, site_for_ip
from monitor import Monitor


class ConfigTests(unittest.TestCase):
    def test_site_mapping_includes_clients_not_just_dc_subnets(self):
        config = load_config()
        self.assertEqual(site_for_ip('10.10.30.101', config), 'SITE-A')
        self.assertEqual(site_for_ip('10.20.40.11', config), 'SITE-B')
        self.assertEqual(site_for_ip('10.20.60.101', config), 'SITE-B')
        self.assertEqual(site_for_ip(None, config), 'Unmapped')

    def test_config_rejects_ip_as_kerberos_dc(self):
        config = load_config()
        config['domain_controller'] = '10.10.10.10'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, 'DNS name'):
                load_config(path)

    def test_explicit_missing_config_does_not_silently_use_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'not found'):
                load_config(Path(directory) / 'missing.json')

    def test_invalid_metric_values_are_never_reported_as_zero(self):
        for value in (None, True, float('nan'), float('inf'), -1, 101, '30'):
            self.assertIsNone(number(value, percent=True))
        self.assertEqual(number(0, percent=True), 0)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.device = copy.deepcopy(self.config['devices'][1])
        self.metrics = {
            'ComputerName': 'FS-NODE1', 'CPUPercent': 0, 'RAMUsedPercent': 50,
            'DiskUsedPercent': 25, 'UptimeHours': 10, 'Checks': {'CPU': 'ok'},
            'Services': [{'Name': name, 'Status': 'Running'} for name in self.device['services']],
        }

    def collect(self, metrics=None, reachable=True):
        with patch('collector.resolve_ip', return_value=self.device['ip']), patch('collector.probe_tcp', return_value=reachable), patch('collector.run_powershell', return_value=metrics or self.metrics):
            return collect_one_device(self.device, self.config)

    def test_remoting_uses_hostname_and_kerberos_not_ip_or_trustedhosts(self):
        script = remote_script(self.device['hostname'], self.config, body='Get-Date')
        self.assertIn("-ComputerName 'FS-NODE1.example.test'", script)
        self.assertIn('-Authentication Kerberos', script)
        self.assertNotIn('TrustedHosts', script)

    def test_healthy_host_with_zero_cpu_is_valid(self):
        device = self.collect()
        self.assertEqual(device['status'], 'ok')
        self.assertEqual(device['cpu'], 0)
        self.assertEqual(device['site'], 'SITE-B')

    def test_remoting_failure_does_not_claim_host_is_offline(self):
        with patch('collector.resolve_ip', return_value=self.device['ip']), patch('collector.probe_tcp', return_value=True), patch('collector.run_powershell', side_effect=CollectionError('Access denied')):
            device = collect_one_device(self.device, self.config)
        self.assertTrue(device['online'])
        self.assertEqual(device['status'], 'metrics_error')
        self.assertIsNone(device['cpu'])

    def test_closed_winrm_port_retains_host_and_unknown_metrics(self):
        device = self.collect(reachable=False)
        self.assertFalse(device['online'])
        self.assertEqual(device['status'], 'unreachable')
        self.assertIn('does not prove', device['error'])
        self.assertIsNone(device['ram'])

    def test_dns_mismatch_never_queries_another_host(self):
        with patch('collector.resolve_ip', return_value='10.20.40.99'), patch('collector.run_powershell') as query:
            device = collect_one_device(self.device, self.config)
        self.assertEqual(device['status'], 'dns_error')
        query.assert_not_called()

    def test_cluster_virtual_ip_is_only_a_tcp_endpoint(self):
        with patch('collector.probe_tcp', return_value=True), patch('collector.resolve_ip', return_value='10.20.40.20'), patch('collector.run_powershell') as query:
            device = collect_one_device(self.config['devices'][-1], self.config)
        query.assert_not_called()
        self.assertEqual(device['status'], 'reachable')
        self.assertIsNone(device['cpu'])

    def test_cluster_and_replication_failures_raise_attention(self):
        metrics = {**self.metrics,
                   'Cluster': {'Nodes': [{'Name': 'FS-NODE2', 'State': 'Down'}], 'Groups': [{'Name': 'FILES', 'State': 'Failed'}]},
                   'Replication': {'FailureCount': 2}}
        device = self.collect(metrics)
        self.assertEqual(device['status'], 'warning')
        self.assertTrue(any('FS-NODE2' in alert for alert in device['alerts']))
        self.assertTrue(any('replication' in alert for alert in device['alerts']))

    def test_partial_telemetry_is_a_warning_with_null_not_zero(self):
        device = self.collect({**self.metrics, 'CPUPercent': None, 'Checks': {'CPU': 'unavailable'}})
        self.assertEqual(device['status'], 'warning')
        self.assertIsNone(device['cpu'])

    def test_single_ad_result_and_role_are_normalised(self):
        data = {'Name': 'DC02', 'DNSHostName': 'dc02.example.test', 'PrimaryGroupID': 516}
        with patch('collector.run_powershell', return_value=data):
            devices = discover_ad_computers(self.config)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]['type'], 'Domain Controller')

    def test_static_metadata_overrides_discovery_without_duplicates(self):
        rows = merge_inventory([self.device], [{'name': 'FS-NODE1', 'hostname': 'fs-node1.example.test', 'kind': 'windows', 'type': 'Server'}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['type'], 'Cluster Node')

    def test_failed_discovery_keeps_previous_inventory_visible(self):
        previous = [{'name': 'MGMT01', 'hostname': 'cla-01.example.test', 'kind': 'windows', 'type': 'Client'}]
        with patch('collector.discover_ad_computers', side_effect=CollectionError('DC unreachable')), patch('collector.collect_one_device', side_effect=lambda d, c: {**d, 'site': 'Unmapped'}):
            result = collect_snapshot(self.config, previous)
        self.assertIn('MGMT01', [d['name'] for d in result['devices']])
        self.assertTrue(result['errors'])

    def test_one_failed_host_is_not_dropped(self):
        config = {**self.config, 'devices': [self.device]}
        with patch('collector.discover_ad_computers', return_value=[]), patch('collector.collect_one_device', side_effect=RuntimeError('failure')):
            result = collect_snapshot(config)
        self.assertTrue(all(row['status'] == 'collection_error' for row in result['devices']))
        self.assertIn('FS-NODE1', [row['name'] for row in result['devices']])

    def test_subprocess_timeout_becomes_explicit_collection_error(self):
        with patch('collector.os.name', 'nt'), patch('collector.subprocess.run', side_effect=subprocess.TimeoutExpired('powershell', 5)):
            with self.assertRaisesRegex(CollectionError, 'timed out'):
                run_powershell('Get-Date')


class ApiTests(unittest.TestCase):
    def test_http_reads_share_one_snapshot_and_do_not_trigger_collection(self):
        config = load_config()
        collector = Mock(return_value={'devices': [{'name': 'FS-NODE1'}], 'errors': [], 'updated_at': utc_now(), 'duration_seconds': 1})
        monitor = Monitor(config, collector)
        monitor.refresh()
        with TestClient(create_app(config, monitor, start_collector=False)) as client:
            for _ in range(3):
                self.assertEqual(client.get('/api/devices').json()[0]['name'], 'FS-NODE1')
            self.assertEqual(client.get('/api/status').json()['domain'], 'example.test')
            self.assertEqual(client.get('/health').json()['collector'], 'running')
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.get('/static/dashboard.js').status_code, 200)
        collector.assert_called_once()

    def test_startup_is_waiting_not_a_fake_healthy_status(self):
        monitor = Monitor(load_config(), Mock())
        with TestClient(create_app(monitor=monitor, start_collector=False)) as client:
            self.assertEqual(client.get('/health').json()['collector'], 'waiting')
            self.assertTrue(client.get('/api/status').json()['stale'])

    def test_failed_cycle_retains_last_success_and_reports_error(self):
        collector = Mock(return_value={'devices': [{'name': 'DC'}], 'errors': [], 'updated_at': utc_now(), 'duration_seconds': 1})
        monitor = Monitor(load_config(), collector)
        monitor.refresh()
        collector.side_effect = RuntimeError('failed')
        monitor.refresh()
        self.assertEqual(monitor.snapshot()['devices'], [{'name': 'DC'}])
        self.assertTrue(monitor.snapshot()['errors'])


if __name__ == '__main__':
    unittest.main()
