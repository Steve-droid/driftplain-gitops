"""B16 inert defaults and bounded independent home-server schedules; no cluster calls."""
import pathlib
import subprocess
import unittest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHART = ROOT / 'charts/home-server-catalog-refresh'
IMAGE = 'ghcr.io/steve-droid/driftplain-backend@sha256:' + 'a' * 64


def render(*settings, success=True):
    command = ['helm', 'template', 'catalog', str(CHART), '-n', 'app']
    for setting in settings:
        command += ['--set', setting]
    result = subprocess.run(command, capture_output=True, text=True)
    if not success:
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


class CatalogRefreshTests(unittest.TestCase):
    def test_defaults_create_nothing_and_are_outside_watched_root(self):
        self.assertEqual(render(), [])
        for path in (ROOT / 'argocd/home-server/apps').glob('*.yaml'):
            self.assertNotIn('home-server-catalog-refresh', path.read_text())

    def test_independent_staggered_jobs_are_suspended_with_bounds(self):
        values = yaml.safe_load((CHART / 'values.yaml').read_text())
        self.assertEqual(len(values['sources']), 19)
        settings = ['enabled=true', f'image={IMAGE}'] + [f'sources.{source}=true' for source in values['sources']]
        docs = render(*settings)
        jobs = [d for d in docs if d['kind'] == 'CronJob']
        self.assertEqual(len(jobs), 19)
        self.assertEqual(len({j['spec']['schedule'] for j in jobs}), 19)
        for job in jobs:
            spec = job['spec']
            self.assertTrue(spec['suspend'])
            self.assertEqual(spec['timeZone'], 'Etc/UTC')
            self.assertEqual(spec['schedule'].split()[1:], ['*/6', '*', '*', '*'])
            self.assertEqual(spec['concurrencyPolicy'], 'Forbid')
            self.assertEqual(spec['startingDeadlineSeconds'], 600)
            workload = spec['jobTemplate']['spec']
            self.assertEqual(workload['backoffLimit'], 0)
            self.assertEqual(workload['activeDeadlineSeconds'], 300)
            pod = workload['template']['spec']
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertEqual(pod['restartPolicy'], 'Never')
            container = pod['containers'][0]
            self.assertEqual(container['image'], IMAGE)
            self.assertEqual(container['command'], ['python', '-m', 'app.catalog.imports.scheduled'])
            self.assertEqual(container['args'][-1], '--refresh')
            self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
            self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
            self.assertEqual(set(container['resources']), {'requests', 'limits'})
            self.assertEqual({e['name'] for e in container['env']}, {'DATABASE_URL', 'PGPASSWORD', 'PYTHONDONTWRITEBYTECODE'})
        self.assertFalse(any(d['kind'] == 'PrometheusRule' for d in docs))

    def test_only_selected_source_and_opt_in_alerts(self):
        docs = render('enabled=true', 'suspend=false', 'monitoring.enabled=true', f'image={IMAGE}', 'sources.testgeneval=true')
        self.assertEqual([d['kind'] for d in docs], ['CronJob', 'PrometheusRule'])
        rule = docs[1]
        content = yaml.safe_dump(rule)
        self.assertIn('testgeneval', content)
        self.assertNotIn('mmlu-pro', content)
        self.assertIn('absent(', content)
        self.assertIn('modelmatch_catalog_pending_review', content)

    def test_invalid_config_fails_closed(self):
        render('enabled=true', 'sources.testgeneval=true', success=False)
        render('sources.unknown=true', success=False)
        render('enabled=true', 'image=backend:latest', success=False)


if __name__ == '__main__':
    unittest.main()
