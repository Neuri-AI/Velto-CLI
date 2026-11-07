"""Declare Python distributions in Qt's generated Buildozer configuration."""
from __future__ import annotations

import configparser
from pathlib import Path
import re
import warnings

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from packaging.requirements import Requirement
from velto_cli.adapters.mobile.qt.android_build_config import ANDROID_BUILD_FILES, ANDROID_OUTPUT_DIRS


def configure_requirements(root: Path, *, uses_velto: bool) -> bool:
    spec = root / 'buildozer.spec'
    if not spec.is_file():
        raise RuntimeError('Qt did not generate buildozer.spec; run --init-spec first')
    config = configparser.ConfigParser(interpolation=None)
    config.read(spec, encoding='utf-8')
    requirements = ['python3', 'shiboken6', 'PySide6']
    project = root / 'pyproject.toml'
    if project.is_file():
        with project.open('rb') as stream:
            dependencies = tomllib.load(stream).get('project', {}).get('dependencies', [])
        for dependency in dependencies:
            requirement = Requirement(dependency)
            if requirement.marker and not requirement.marker.evaluate({'sys_platform': 'android', 'platform_system': 'Android'}):
                continue
            if requirement.url or requirement.extras:
                raise ValueError(f'Android dependency requires explicit support: {dependency}')
            if requirement.name.lower().replace('_', '-') == 'velto':
                uses_velto = True
            exact = list(requirement.specifier)
            if len(exact) > 1:
                warnings.warn(
                    f'Buildozer cannot represent all version bounds for {requirement.name}: '
                    f'{requirement.specifier}; resolving by package name', RuntimeWarning,
                )
            suffix = str(exact[0]) if len(exact) == 1 else ''
            requirements.append(requirement.name + suffix)
    if uses_velto:
        requirements.extend(['velto', 'qyro-engine', 'velto-core',
                             'velto_cinnamon_core', 'resvg_py'])
    unique = []
    seen = set()
    for value in requirements:
        key = re.split(r'[<>=!~]', value, 1)[0].lower().replace('_', '-')
        if key not in seen:
            unique.append(value)
            seen.add(key)
    config.set('app', 'requirements', ','.join(unique))
    # Qt still needs deployment/ on the build host to construct its recipes,
    # but neither that directory nor project build metadata belongs in the APK.
    for key, excluded in (
        ('source.exclude_dirs', sorted(ANDROID_OUTPUT_DIRS)),
        ('source.exclude_patterns', sorted(ANDROID_BUILD_FILES) + ['readme', 'readme.*', 'license', 'license.*']),
    ):
        existing = [item.strip() for item in config.get('app', key, fallback='').split(',') if item.strip()]
        present = {item.lower() for item in existing}
        existing.extend(item for item in excluded if item.lower() not in present)
        config.set('app', key, ','.join(existing))
    with spec.open('w', encoding='utf-8') as stream:
        config.write(stream)
    return uses_velto
