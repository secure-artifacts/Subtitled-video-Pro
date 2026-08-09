import tempfile
import unittest
from pathlib import Path

import app_storage


class AppStorageTests(unittest.TestCase):
    def test_env_config_dir_override(self):
        env = {app_storage.ENV_CONFIG_DIR: "/tmp/subtitle-config"}

        self.assertEqual(app_storage.app_config_dir(env=env), Path("/tmp/subtitle-config"))

    def test_workspace_data_dir(self):
        root = Path(tempfile.gettempdir()) / "subtitle-project"

        self.assertEqual(app_storage.workspace_data_dir(root), root.resolve() / ".subtitle_composer")

    def test_portable_dir_is_default_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            portable = Path(tmp) / "portable-user-data"
            env = {app_storage.ENV_PORTABLE_DIR: str(portable)}

            self.assertEqual(app_storage.app_config_dir(env=env), portable)
            self.assertEqual(app_storage.app_data_dir(env=env), portable)
            self.assertEqual(app_storage.app_state_dir(env=env), portable / "State")
            self.assertEqual(app_storage.app_cache_dir(env=env), portable / "Cache")

    def test_home_override_disables_portable_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            portable = Path(tmp) / "portable"
            env = {app_storage.ENV_HOME: str(home), app_storage.ENV_PORTABLE_DIR: str(portable)}

            self.assertEqual(app_storage.app_config_dir(env=env), home / app_storage.APP_NAME)
            self.assertEqual(app_storage.app_state_dir(env=env), home / app_storage.APP_NAME / "State")

    def test_migrate_legacy_file_copies_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legacy = tmp_path / "style_presets.json"
            target = tmp_path / "new" / "style_presets.json"
            legacy.write_text('{"a": 1}', encoding="utf-8")

            self.assertTrue(app_storage.migrate_legacy_file(legacy, target))
            self.assertEqual(target.read_text(encoding="utf-8"), '{"a": 1}')

            target.write_text('{"b": 2}', encoding="utf-8")
            self.assertFalse(app_storage.migrate_legacy_file(legacy, target))
            self.assertEqual(target.read_text(encoding="utf-8"), '{"b": 2}')

    def test_resolve_user_file_migrates_from_legacy_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legacy_root = tmp_path / "legacy"
            data_root = tmp_path / "data"
            legacy_root.mkdir()
            (legacy_root / "signature_presets.json").write_text("{}", encoding="utf-8")
            env = {app_storage.ENV_DATA_DIR: str(data_root), app_storage.ENV_CONFIG_DIR: str(data_root)}

            target = app_storage.resolve_user_file(
                "signature_presets.json",
                legacy_root=legacy_root,
                kind="config",
                env=env,
            )

            self.assertTrue(target.exists())
            self.assertEqual(target.parent, data_root)

    def test_resolve_user_file_migrates_from_standard_config_to_portable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            standard_root = tmp_path / "standard"
            portable_root = tmp_path / "portable"
            standard_root.mkdir()
            (standard_root / app_storage.APP_NAME).mkdir()
            (standard_root / app_storage.APP_NAME / "settings.json").write_text('{"restored": true}', encoding="utf-8")
            env = {"APPDATA": str(standard_root), app_storage.ENV_PORTABLE_DIR: str(portable_root)}

            target = app_storage.resolve_user_file("settings.json", kind="config", env=env)

            self.assertEqual(target, portable_root / "settings.json")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), '{"restored": true}')

    def test_json_helpers_read_default_and_write_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "settings.json"

            self.assertEqual(app_storage.read_json_file(target, default={"missing": True}), {"missing": True})
            app_storage.write_json_file(target, {"ok": True})

            self.assertEqual(app_storage.read_json_file(target), {"ok": True})
            self.assertFalse(target.with_name("settings.json.tmp").exists())

    def test_known_user_files_honor_explicit_env_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                app_storage.ENV_CONFIG_DIR: str(Path(tmp) / "config"),
                app_storage.ENV_STATE_DIR: str(Path(tmp) / "state"),
                app_storage.ENV_CACHE_DIR: str(Path(tmp) / "cache"),
            }
            files = app_storage.known_user_files(legacy_root=tmp, env=env)

            self.assertEqual(files["style_presets"].parent, Path(tmp) / "config")
            self.assertEqual(files["signature_presets"].parent, Path(tmp) / "config")
            self.assertEqual(files["layout_presets"].parent, Path(tmp) / "config")
            self.assertEqual(files["title_caption_presets"].parent, Path(tmp) / "config")
            self.assertEqual(files["caption_mode_presets"].parent, Path(tmp) / "config")
            self.assertEqual(files["effects"].parent, Path(tmp) / "config")
            self.assertEqual(files["settings"].parent, Path(tmp) / "config")
            self.assertEqual(files["batch_queue_backups"].parent, Path(tmp) / "state")
            self.assertEqual(files["export_queue_backups"].parent, Path(tmp) / "state")
            self.assertEqual(files["project_cache"].parent, Path(tmp) / "cache")
            self.assertEqual(files["edit_project_cache"].parent, Path(tmp) / "cache")


if __name__ == "__main__":
    unittest.main()
