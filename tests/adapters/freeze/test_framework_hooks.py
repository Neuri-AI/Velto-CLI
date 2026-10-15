from pathlib import Path

import pytest

from qyro_cli.adapters.freeze.framework_hooks import FrameworkHookResolver
from qyro_cli.adapters.freeze.pyinstaller_adapter import PyInstallerFreezer
from qyro_cli.domain.build import FreezeManifest
from qyro_cli.domain.project import Binding


@pytest.mark.parametrize("binding", list(Binding))
def test_freeze_collects_lazy_qyro_adapters_for_every_binding(tmp_path, binding):
    manifest = FreezeManifest(
        app_name="MyApp",
        binding=binding.value,
        entry_point="main.py",
        target_platform="windows",
    )

    args = FrameworkHookResolver().resolve_args(tmp_path, manifest)
    collected = [args[i + 1] for i, arg in enumerate(args) if arg == "--collect-submodules"]
    excluded = [args[i + 1] for i, arg in enumerate(args) if arg == "--exclude-module"]

    assert "qyro.adapters.frameworks" in collected
    assert not any(module.startswith("qyro.adapters.frameworks") for module in excluded)
    assert binding.import_name not in excluded
    for other in Binding:
        if other != binding:
            assert other.import_name in excluded


def test_collected_qyro_adapters_cover_factory_registry():
    from PyInstaller.utils.hooks import collect_submodules
    from qyro.adapters.frameworks.factory import FrameworkFactory

    modules = set(collect_submodules("qyro.adapters.frameworks"))

    for module_name, _ in FrameworkFactory._FRAMEWORK_REGISTRY.values():
        assert f"qyro.adapters.frameworks{module_name}" in modules


def test_freeze_prepares_psx_lexical_state_and_callbacks(tmp_path, monkeypatch):
    pytest.importorskip("psx")
    from psx.core.reconcile import Reconciler
    from psx.renderers.headless import HeadlessRenderer

    source = '''from psx import component, psx, use_state
@component
def Counter():
    count, set_count = use_state(10)
    def increment():
        set_count(lambda value: value + 1)
    return psx("<Column><Text>{count}</Text><Button on_click={increment}>+</Button></Column>")
'''
    entry = tmp_path / "main.py"
    entry.write_text(source, encoding="utf-8")
    freezer = PyInstallerFreezer(FrameworkHookResolver())
    monkeypatch.setattr(
        freezer, "_prepare_protected_secrets_package",
        lambda **kwargs: (tmp_path / "resources.pak", tmp_path / "runtime.pyd"),
    )
    manifest = FreezeManifest(
        app_name="Counter", binding="PySide6", entry_point="main.py",
        target_platform="windows", protect_resources=False,
    )

    cmd = freezer.build_command(tmp_path, manifest)
    generated = Path(cmd[3])
    assert generated != entry
    assert generated.is_file()
    assert cmd[cmd.index("--paths") + 1] == str(tmp_path)
    assert entry.read_text(encoding="utf-8") == source

    namespace = {"__name__": "frozen_counter"}
    exec(compile(generated.read_text(encoding="utf-8"), str(generated), "exec"), namespace)
    renderer = HeadlessRenderer()
    root = Reconciler(renderer).render(namespace["Counter"]())
    label, button = root.children[0].handle.children
    assert label.props["value"] == "10"
    button.events["on_click"].invoke()
    renderer.flush()
    assert label.props["value"] == "11"


def test_pyside2_collects_runtime_binaries_and_data():
    resolver = FrameworkHookResolver()
    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="windows",
    )

    args = resolver.resolve_args(Path("."), manifest)

    assert "--collect-all" in args
    assert "PySide2" in args[args.index("--collect-all") + 1]
    assert "shiboken2" in args


def test_build_command_includes_windows_qt_runtime_paths(tmp_path):
    project_root = tmp_path
    (project_root / "main.py").write_text("print('hello')\n", encoding="utf-8")
    settings_dir = project_root / "settings"
    settings_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text('{"app_name":"MyApp"}', encoding="utf-8")
    (settings_dir / "secrets.json").write_text('{"token":"x"}', encoding="utf-8")

    freezer = PyInstallerFreezer(FrameworkHookResolver())
    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="windows",
    )

    cmd = freezer.build_command(project_root, manifest)

    assert cmd[3] == str(project_root / "main.py")
    assert "--collect-all" in cmd
    assert "PySide2" in cmd


def test_framework_hooks_skip_plain_assets_when_protection_enabled(tmp_path):
    project_root = tmp_path
    settings_dir = project_root / "settings"
    resources_dir = project_root / "resources"
    settings_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text("{}", encoding="utf-8")
    (resources_dir / "logo.png").write_text("bin", encoding="utf-8")

    resolver = FrameworkHookResolver()
    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="windows",
        protect_resources=True,
    )

    args = resolver.resolve_args(project_root, manifest)

    add_data_values = [args[i + 1] for i, token in enumerate(args) if token == "--add-data"]
    assert not any(value.endswith(":settings") or value.endswith(";settings") for value in add_data_values)
    assert not any(value.endswith(":resources") or value.endswith(";resources") for value in add_data_values)


def test_framework_hooks_exclude_local_secret_and_signing_files(tmp_path):
    project_root = tmp_path
    settings_dir = project_root / "settings"
    resources_dir = project_root / "resources"
    settings_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text("{}", encoding="utf-8")
    (settings_dir / "secrets.json").write_text('{"password":"local"}', encoding="utf-8")
    (settings_dir / "sign.json").write_text(
        '{"sign":{"windows":{"password":"signing-secret"}}}',
        encoding="utf-8",
    )
    (resources_dir / "logo.png").write_text("bin", encoding="utf-8")

    resolver = FrameworkHookResolver()
    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="windows",
        protect_resources=False,
    )

    args = resolver.resolve_args(project_root, manifest)
    add_data_values = [args[i + 1] for i, token in enumerate(args) if token == "--add-data"]

    assert any("base.json" in value for value in add_data_values)
    assert not any("secrets.json" in value for value in add_data_values)
    assert not any("sign.json" in value for value in add_data_values)


@pytest.mark.parametrize("bundle_path", [".qyro/resources.pak", "custom/assets.pak"])
def test_build_command_embeds_protected_bundle_and_runtime_module(tmp_path, monkeypatch, bundle_path):
    import shutil
    import sys
    from qyro.adapters.environment.system_environment import SystemEnvironmentAdapter
    from qyro.adapters.settings.json_settings import JsonSettingsAdapter
    from qyro.adapters.resources.protected_bundle import ProtectedResourceBundle

    project_root = tmp_path
    (project_root / "main.py").write_text("print('hello')\n", encoding="utf-8")
    settings_dir = project_root / "settings"
    resources_dir = project_root / "resources"
    settings_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    (settings_dir / "base.json").write_text('{"app_name": "MyApp", "binding": "PySide6"}', encoding="utf-8")
    (settings_dir / "secrets.json").write_text('{"api": {"token": "local-secret"}}', encoding="utf-8")
    (resources_dir / "logo.png").write_text("png", encoding="utf-8")

    freezer = PyInstallerFreezer(FrameworkHookResolver())
    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="windows",
        protect_resources=True,
        protected_bundle_relative_path=bundle_path,
    )

    cmd = freezer.build_command(project_root, manifest)

    pak_path = project_root / bundle_path
    external_secrets_path = project_root / ".qyro" / "secrets.json"
    runtime_dir = pak_path.parent

    assert pak_path.exists()
    assert not external_secrets_path.exists()
    runtime_candidates = list(runtime_dir.glob("runtime*.so")) + list(runtime_dir.glob("runtime*.pyd"))
    assert runtime_candidates
    assert "--add-data" in cmd
    add_data_values = [cmd[i + 1] for i, token in enumerate(cmd) if token == "--add-data"]
    assert any(".qyro/resources.pak" in value for value in add_data_values)
    assert any("runtime" in value and (".so" in value or ".pyd" in value) for value in add_data_values)
    assert not any(".qyro/secrets.json" in value for value in add_data_values)

    # Simulate PyInstaller's data layout and read it through the real runtime:
    # neither plaintext settings nor a source working directory is available.
    frozen_root = tmp_path / "frozen"
    for value in add_data_values:
        source, destination = value.rsplit(";", 1)
        target = frozen_root / destination
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_root), raising=False)
    env = SystemEnvironmentAdapter()
    settings = JsonSettingsAdapter(env).get_raw_settings()
    assert settings["binding"] == "PySide6"
    assert settings["api"]["token"] == "local-secret"
    extracted = ProtectedResourceBundle.get_extracted_root(env)
    assert (extracted / "resources/logo.png").read_text(encoding="utf-8") == "png"


def test_copy_mac_resources_skips_plain_resources_when_protected(tmp_path):
    project_root = tmp_path
    app_bundle = project_root / "build" / "MyApp.app"
    resources_dest = app_bundle / "Contents" / "Resources"
    resources_dest.mkdir(parents=True)

    source = project_root / "resources" / "linux" / "icons"
    source.mkdir(parents=True)
    (source / "128.png").write_text("png", encoding="utf-8")

    freezer = PyInstallerFreezer(FrameworkHookResolver())
    manifest = FreezeManifest(
        app_name="MyApp",
        binding="PySide2",
        entry_point="main.py",
        target_platform="mac",
        protect_resources=True,
    )

    freezer._copy_mac_resources(project_root, app_bundle, manifest)

    assert not (resources_dest / "linux" / "icons" / "128.png").exists()
