"""Run Qt deployment and add project dependencies after Qt generates its spec."""
import runpy
import sys
import os
from pathlib import Path

import PySide6

scripts = Path(PySide6.__file__).parent / 'scripts'
sys.path.insert(0, str(scripts))
from deploy_lib.android.buildozer import Buildozer
from velto_cli.adapters.mobile.qt.android_requirements import configure_requirements
from velto_cli.adapters.mobile.qt.android_sources import uses_velto
from velto_cli.adapters.mobile.qt.android_local_wheels import local_wheels
from velto_cli.adapters.mobile.qt.android_permissions import configure_android_permissions
from velto_cli.adapters.mobile.qt.features import enabled_qt_android_features
from velto_cli.adapters.mobile.qt.android_wheel_recipes import configure_velto_wheel_recipes

initialize = Buildozer.initialize
configured = False
if len(sys.argv) < 3 or sys.argv[1] != '--velto-entry':
    raise SystemExit('Android deployment requires a configured entry point')
entry = sys.argv[2]
del sys.argv[1:3]

def initialize_with_dependencies(pysidedeploy_config):
    global configured
    initialize(pysidedeploy_config)
    root = Path(pysidedeploy_config.project_dir)
    source = (root / entry).resolve()
    if not source.is_relative_to(root.resolve()):
        raise ValueError('Android entry_point must stay inside the project')
    enabled = configure_requirements(root, uses_velto=uses_velto(source))
    features = enabled_qt_android_features(root)
    features.configure_buildozer(root)
    configure_android_permissions(root, features.required_permissions)
    overrides = local_wheels(root)
    if enabled or overrides:
        configure_velto_wheel_recipes(Path(pysidedeploy_config.recipe_dir), local_wheels=overrides)
    configured = True

Buildozer.initialize = staticmethod(initialize_with_dependencies)
runpy.run_path(str(scripts / 'android_deploy.py'), run_name='__main__')
if not configured:
    raise SystemExit('Qt Android deployment did not configure project dependencies')
