# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from abralia.backend import plugin_install as install
from abralia.backend.plugin_migration import apply_project_migration, preview_project_migration
from abralia.backend.project import enable_project


PUBLIC_ROOT = Path(__file__).resolve().parents[3]
BUNDLE = PUBLIC_ROOT / 'abralia/desktop/plugin-bundle'


class FakeCodex:
    def __init__(self):
        self.marketplaces = {'unrelated': '/untouched'}
        self.plugins = {'other@unrelated': {'pluginId': 'other@unrelated', 'version': '9'}}
        self.calls = []
        self.fail_add = False
        self.before_add = None

    def __call__(self, executable, args, cwd):
        self.calls.append(tuple(args))
        if args[:2] == ['marketplace', 'list']:
            return {'marketplaces': [{'name': name, 'root': root} for name, root in self.marketplaces.items()]}
        if args[:2] == ['marketplace', 'add']:
            self.marketplaces['abralia'] = args[2]
            return {'marketplaceName': 'abralia', 'alreadyAdded': False}
        if args[:2] == ['marketplace', 'remove']:
            self.marketplaces.pop(args[2], None)
            return {'marketplaceName': args[2]}
        if args[0] == 'list':
            return {'installed': [item for key, item in self.plugins.items() if key.endswith('@abralia')], 'available': []}
        if args[0] == 'add':
            if self.before_add:
                self.before_add()
            root = Path(self.marketplaces['abralia'])
            version = json.loads((root / 'plugins/abralia/.codex-plugin/plugin.json').read_text())['version']
            self.plugins[install.PLUGIN_ID] = {'pluginId': install.PLUGIN_ID, 'version': version,
                                               'installed': True, 'enabled': True}
            if self.fail_add:
                self.fail_add = False
                raise install.PluginInstallError('fixture_lost_install_ack')
            return self.plugins[install.PLUGIN_ID]
        if args[0] == 'remove':
            self.plugins.pop(args[1], None)
            return {'pluginId': args[1]}
        raise AssertionError(args)


class PluginInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abralia-plugin-test-', dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'Abralia/control'
        self.bundle = self.root / 'bundle'
        shutil.copytree(BUNDLE, self.bundle)
        runtime = self.root / 'frozen-runtime'
        (runtime / '_internal').mkdir(parents=True)
        (runtime / '_internal/resources').write_text('fixture runtime support')
        self.runtime = runtime / 'abralia-gui-host'
        self.runtime.write_text('#!/bin/sh\n[ "$1" = mcp ] && [ "$2" = --help ]\n')
        self.runtime.chmod(0o700)
        self.codex = self.root / 'codex'
        self.codex.write_text('#!/bin/sh\nexit 99\n')
        self.codex.chmod(0o700)
        self.cli = FakeCodex()
        self.addCleanup(patch.stopall)
        patch.object(install, '_codex', self.cli).start()

    def install(self, **kwargs):
        return install.install_plugin(self.bundle, self.runtime, self.state, self.codex, **kwargs)

    def test_install_is_idempotent_and_uses_copied_runtime_not_checkout(self):
        first = self.install()
        self.assertEqual(first['status'], 'configured')
        self.assertTrue(first['installed'])
        self.assertEqual(first['trust'], 'review_required_or_unknown')
        self.assertTrue(first['runtime_available'])
        paths = install._paths(self.state)
        launcher = paths['launcher'].read_text()
        self.assertNotIn(str(self.runtime), launcher)
        self.assertNotIn('python', launcher)
        self.assertTrue((self.state.parent / 'runtimes').is_dir())
        self.assertEqual(paths['launcher'].stat().st_mode & 0o777, 0o700)
        self.assertEqual(paths['manifest'].stat().st_mode & 0o777, 0o600)
        before = sum(args[0] == 'add' for args in self.cli.calls)
        self.assertFalse(self.install()['changed'])
        self.assertEqual(sum(args[0] == 'add' for args in self.cli.calls), before)
        shutil.rmtree(self.runtime.parent)
        self.assertTrue(install.inspect_installation(self.state)['runtime_available'])
        self.assertEqual(self.cli.marketplaces['unrelated'], '/untouched')

    def test_inspection_never_spawns_codex_without_explicit_verification(self):
        self.install()
        before = list(self.cli.calls)
        result = install.inspect_installation(self.state)
        self.assertEqual(result['verification'], 'local_receipt')
        self.assertEqual(self.cli.calls, before)
        self.assertTrue(install.inspect_installation(self.state, self.codex, verify=True)['installed'])
        self.assertGreater(len(self.cli.calls), len(before))

    def test_uninstall_only_removes_our_plugin_and_marketplace(self):
        self.install()
        result = install.uninstall_plugin(self.state, self.codex)
        self.assertEqual(result['status'], 'removed')
        self.assertFalse(result['installed'])
        self.assertTrue(result['runtime_retained'])
        self.assertEqual(set(self.cli.plugins), {'other@unrelated'})
        self.assertEqual(self.cli.marketplaces, {'unrelated': '/untouched'})
        self.assertFalse(install.uninstall_plugin(self.state, self.codex)['changed'])

    def test_collision_refuses_to_replace_another_abralia_marketplace(self):
        self.cli.marketplaces['abralia'] = '/somebody-elses/abralia'
        with self.assertRaisesRegex(install.PluginInstallError, 'marketplace_name_conflict'):
            self.install()
        self.assertFalse(any(args[0] in ('add', 'remove') for args in self.cli.calls))
        self.assertEqual(self.cli.marketplaces['abralia'], '/somebody-elses/abralia')

    def test_edited_launcher_and_marketplace_are_preserved(self):
        self.install()
        paths = install._paths(self.state)
        paths['launcher'].write_text('user replacement')
        with self.assertRaisesRegex(install.PluginInstallError, 'managed_launcher_conflict'):
            self.install()
        self.assertEqual(paths['launcher'].read_text(), 'user replacement')

    def test_failed_install_ack_rolls_back_only_abralia(self):
        self.cli.fail_add = True
        with self.assertRaisesRegex(install.PluginInstallError, 'fixture_lost_install_ack'):
            self.install()
        self.assertNotIn('abralia', self.cli.marketplaces)
        self.assertNotIn(install.PLUGIN_ID, self.cli.plugins)
        self.assertEqual(self.cli.marketplaces['unrelated'], '/untouched')
        self.assertFalse(install._paths(self.state)['launcher'].exists())
        self.assertFalse(install.inspect_installation(self.state)['installed'])

    def test_upgrade_failure_restores_previous_plugin_bundle_and_launcher(self):
        original_version = self.install()['version']
        paths = install._paths(self.state)
        launcher = paths['launcher'].read_text()
        manifest = self.bundle / 'plugins/abralia/.codex-plugin/plugin.json'
        data = json.loads(manifest.read_text()); data['version'] = '0.2.0'; manifest.write_text(json.dumps(data))
        self.runtime.write_text(self.runtime.read_text() + '# next runtime\n')
        self.cli.fail_add = True
        with self.assertRaisesRegex(install.PluginInstallError, 'fixture_lost_install_ack'):
            self.install()
        self.assertEqual(self.cli.plugins[install.PLUGIN_ID]['version'], original_version)
        self.assertEqual(paths['launcher'].read_text(), launcher)
        self.assertEqual(install.inspect_installation(self.state)['version'], original_version)

    def test_concurrent_launcher_edit_is_not_erased_by_rollback(self):
        paths = install._paths(self.state)
        self.cli.before_add = lambda: paths['launcher'].write_text('concurrent user edit')
        self.cli.fail_add = True
        with self.assertRaisesRegex(install.PluginInstallError, 'rollback_incomplete'):
            self.install()
        self.assertEqual(paths['launcher'].read_text(), 'concurrent user edit')

    def test_bundle_escape_and_runtime_escape_are_rejected(self):
        manifest = self.bundle / '.agents/plugins/marketplace.json'
        data = json.loads(manifest.read_text()); data['plugins'][0]['source']['path'] = '../outside'
        manifest.write_text(json.dumps(data))
        with self.assertRaises(install.PluginInstallError):
            self.install()
        (self.runtime.parent / '_internal/escape').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(install.PluginInstallError, 'symlink_escapes'):
            install.stage_runtime(self.runtime, self.state)

    def test_runtime_smoke_failure_happens_before_codex_mutation(self):
        self.runtime.write_text('#!/bin/sh\nexit 2\n')
        with self.assertRaisesRegex(install.PluginInstallError, 'validation_failed'):
            self.install()
        self.assertFalse(any(args[0] in ('add', 'remove') or args[:2] == ('marketplace', 'add') for args in self.cli.calls))

    def test_manual_hook_fallback_is_read_only_named_and_independent_of_plugin_cache(self):
        text = install.render_manual_hooks(self.bundle, self.state)
        parsed = tomllib.loads(text)
        self.assertEqual(len(parsed['hooks']), 6)
        for groups in parsed['hooks'].values():
            for group in groups:
                handler = group['hooks'][0]
                self.assertTrue(handler['statusMessage'].startswith('Abralia'))
                self.assertIn('abralia-host', handler['command'])
                self.assertIn('codex-hook', handler['command'])
                self.assertNotIn('PLUGIN_ROOT', handler['command'])
        self.assertIn('do not enable both', text)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.cli.calls)

    def test_migration_requires_backup_before_any_codex_mutation(self):
        with self.assertRaisesRegex(install.PluginInstallError, 'backup_directory_required'):
            self.install(migrate_project=self.root)
        self.assertFalse(self.cli.calls)

    def test_idempotent_install_still_applies_explicit_requested_migration(self):
        self.install()
        project = self.root / 'project'; project.mkdir()
        enable_project(project, python='/fixture/python')
        result = self.install(migrate_project=project, migration_backup_dir=self.root / 'backups')
        self.assertFalse(result['changed'])
        self.assertEqual(result['migration']['status'], 'migrated')
        self.assertNotIn('abralia_experiment', (project / '.codex/config.toml').read_text())


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abralia-migration-test-', dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        (self.project / '.codex').mkdir()
        self.config = self.project / '.codex/config.toml'
        self.unrelated = '# user setting\n[features]\nuser_choice = true\n'
        self.config.write_text(self.unrelated)
        enable_project(self.project, python='/fixture/python')
        spec = importlib.util.spec_from_file_location('fixture_probe', PUBLIC_ROOT / 'experiments/codex-question-hooks/probe.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.hooks = module.configuration_block(self.project, self.root / 'journal.jsonl', '/fixture/python')
        self.config.write_text(self.config.read_text() + self.hooks)
        self.backups = self.root / 'backups'

    def test_preview_is_read_only_and_apply_backs_up_only_owned_content(self):
        before = self.config.read_text()
        plan = preview_project_migration(self.project)
        self.assertEqual(self.config.read_text(), before)
        self.assertEqual(plan.summary()['managed_blocks'], 2)
        result = apply_project_migration(plan, self.backups)
        self.assertEqual(result['status'], 'migrated')
        self.assertEqual(tomllib.loads(self.config.read_text()), tomllib.loads(self.unrelated))
        self.assertFalse((self.project / '.agents/skills/abralia/SKILL.md').exists())
        self.assertFalse((self.project / '.codex/abralia-project.json').exists())
        backup = Path(result['backup_path'])
        self.assertNotIn('user_choice', backup.read_text())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(apply_project_migration(preview_project_migration(self.project), self.backups)['status'], 'unchanged')

    def test_modified_skill_is_preserved(self):
        skill = self.project / '.agents/skills/abralia/SKILL.md'
        skill.write_text(skill.read_text() + '\nUser addition')
        result = apply_project_migration(preview_project_migration(self.project), self.backups)
        self.assertIn(str(skill), result['preserved_user_files'])
        self.assertTrue(skill.read_text().endswith('User addition'))

    def test_edited_managed_hook_or_stale_preview_never_overwrites_config(self):
        plan = preview_project_migration(self.project)
        self.config.write_text(self.config.read_text() + '\n# concurrent edit\n')
        with self.assertRaisesRegex(install.PluginInstallError, 'changed_since'):
            apply_project_migration(plan, self.backups)
        self.assertTrue(self.config.read_text().endswith('# concurrent edit\n'))
        self.config.write_text(self.config.read_text().replace('timeout = 2', 'timeout = 3'))
        with self.assertRaisesRegex(install.PluginInstallError, 'hook_block_conflict'):
            preview_project_migration(self.project)

    def test_rollback_preserves_concurrent_unrelated_configuration_edit(self):
        plan = preview_project_migration(self.project)
        manifest = self.project / '.codex/abralia-project.json'
        real_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == manifest:
                self.config.write_text(self.config.read_text() + '\n# new concurrent setting\n')
                raise OSError('fixture deletion failure')
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink):
            with self.assertRaisesRegex(OSError, 'fixture deletion failure'):
                apply_project_migration(plan, self.backups)
        after = self.config.read_text()
        self.assertIn('# new concurrent setting', after)
        self.assertIn('[mcp_servers.abralia_experiment]', after)
        self.assertIn('Abralia Codex hook probe', after)
        self.assertTrue((self.project / '.agents/skills/abralia/SKILL.md').exists())
        tomllib.loads(after)


if __name__ == '__main__':
    unittest.main()
