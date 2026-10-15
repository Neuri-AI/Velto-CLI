
from unittest.mock import patch

from velto_cli.adapters.package.metadata import PackageMetadata


@patch("velto_cli.adapters.package.metadata.version")
def test_cli_version(mock_version):
    mock_version.return_value = "1.2.3"
    metadata = PackageMetadata()
    assert metadata.cli_version() == "1.2.3"
    mock_version.assert_called_once_with("velto-cli")


@patch("velto_cli.adapters.package.metadata.version")
def test_engine_version(mock_version):
    mock_version.return_value = "1.2.3"
    metadata = PackageMetadata()
    assert metadata.engine_version() == "1.2.3"
    mock_version.assert_called_once_with("qyro-engine")


@patch("velto_cli.adapters.package.metadata.version")
def test_velto_version(mock_version):
    mock_version.return_value = "1.2.3"
    metadata = PackageMetadata()
    assert metadata.velto_version() == "1.2.3"
