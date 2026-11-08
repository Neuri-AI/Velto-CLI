import configparser
import io
import tarfile
from zipfile import ZipFile

import pytest

from velto_cli.adapters.mobile.qt.android_requirements import configure_requirements
from velto_cli.adapters.mobile.qt.android_sources import verify_velto_apk_sources


def test_project_dependencies_are_declared_for_p4a(tmp_path):
    (tmp_path / 'buildozer.spec').write_text('[app]\nrequirements = python3,PySide6\n')
    (tmp_path / 'pyproject.toml').write_text('[project]\ndependencies = ["velto>=0.1.0,<1.0.0", "requests==2.32.0"]\n')
    configure_requirements(tmp_path, uses_velto=False)
    config = configparser.ConfigParser()
    config.read(tmp_path / 'buildozer.spec')
    assert config['app']['requirements'].split(',') == [
        'python3', 'shiboken6', 'PySide6', 'velto', 'requests==2.32.0',
        'qyro-engine', 'velto-core', 'velto_cinnamon_core', 'resvg_py',
    ]
    assert 'deployment' in config['app']['source.exclude_dirs'].split(',')
    assert 'pyproject.toml' in config['app']['source.exclude_patterns'].split(',')


def test_protected_resources_do_not_require_unavailable_cryptography_wheel(tmp_path):
    (tmp_path / 'settings').mkdir()
    (tmp_path / 'buildozer.spec').write_text('[app]\nrequirements = python3,PySide6\n')
    (tmp_path / 'pyproject.toml').write_text('[project]\ndependencies = ["velto"]\n')
    (tmp_path / 'settings' / 'build.json').write_text(
        '{"resource_protection":{"enabled":true}}'
    )
    configure_requirements(tmp_path, uses_velto=False)
    config = configparser.ConfigParser()
    config.read(tmp_path / 'buildozer.spec')
    assert 'cryptography' not in config['app']['requirements'].split(',')


def test_verifies_installed_packages_in_android_bundle(tmp_path):
    apk = tmp_path / 'app.apk'
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        for package in ('velto', 'qyro', 'psx'):
            member = tarfile.TarInfo(f'_python_bundle/site-packages/{package}/__init__.py')
            member.size = 1
            archive.addfile(member, io.BytesIO(b' '))
        for name in ("velto", "qyro_engine", "velto_core", "velto_cinnamon_core", "resvg_py"):
            member = tarfile.TarInfo(f"_python_bundle/site-packages/{name}-0.1.1.dist-info/METADATA")
            member.size = 1
            archive.addfile(member, io.BytesIO(b" "))
        member = tarfile.TarInfo('_python_bundle/site-packages/_sfle_native/_sfle_native.so')
        member.size = 1
        archive.addfile(member, io.BytesIO(b' '))
    with ZipFile(apk, 'w') as archive:
        archive.writestr('lib/arm64-v8a/libpybundle.so', data.getvalue())
    verify_velto_apk_sources(apk)
    with pytest.raises(RuntimeError, match='no Python distribution'):
        empty = tmp_path / 'empty.apk'
        with ZipFile(empty, 'w'):
            pass
        verify_velto_apk_sources(empty)


def test_cinnamon_recipe_name_matches_p4a_normalized_distribution(tmp_path):
    from packaging.requirements import Requirement
    from velto_cli.adapters.mobile.qt.android_wheel_recipes import configure_velto_wheel_recipes

    configure_velto_wheel_recipes(tmp_path)
    recipe = tmp_path / 'velto_cinnamon_core' / '__init__.py'
    assert recipe.is_file()
    recipe_names = [Requirement(recipe.parent.name).name]
    distribution_name = 'velto-cinnamon-core'.lower().replace('-', '_')
    assert distribution_name in recipe_names
    assert "'velto-cinnamon-core'" in recipe.read_text()


def test_android_splash_waits_for_qt_surface_without_python_jni(tmp_path, monkeypatch):
    from velto_cli.adapters.mobile.qt.android import QtAndroidBuilder

    distribution = tmp_path / "distribution"
    manifest = distribution / "src/main/AndroidManifest.xml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
        '<application><activity android:name="org.kivy.android.PythonActivity"/>'
        '</application></manifest>', encoding="utf-8",
    )
    (distribution / "build.gradle").write_text("dependencies {\n}\n", encoding="utf-8")
    (distribution / "gradlew").touch()
    artifact = tmp_path / "app.apk"
    artifact.touch()
    monkeypatch.setattr("velto_cli.adapters.mobile.qt.android._gradle_directory",
                        lambda _artifact: distribution)
    calls = []
    builder = QtAndroidBuilder()
    monkeypatch.setattr(builder, "_run", lambda args, cwd: calls.append((args, cwd)),
                        raising=False)

    assert builder._install_android_splash_bridge(artifact, tmp_path, debug=True)
    java = (distribution / "src/main/java/org/velto/SplashPythonActivity.java").read_text()
    assert "findQtSurface" in java
    assert "org.qtproject.qt.android.QtSurface" in java
    assert "surfaceCreated" in java
    assert "jnius" not in java
    assert calls
