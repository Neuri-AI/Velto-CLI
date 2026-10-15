
from unittest.mock import patch

from qyro_cli.adapters.package.metadata import PackageMetadata


@patch("qyro_cli.adapters.package.metadata.version")
def test_cli_version(mock_version):
    mock_version.return_value = "1.2.3"
    metadata = PackageMetadata()
    assert metadata.cli_version() == "1.2.3"
    mock_version.assert_called_once_with("qyro-cli")


@patch("qyro_cli.adapters.package.metadata.version")
def test_engine_version(mock_version):
    mock_version.return_value = "1.2.3"
    metadata = PackageMetadata()
    assert metadata.engine_version() == "1.2.3"
    mock_version.assert_called_once_with("qyro-engine")


@patch("qyro_cli.adapters.package.metadata.version")
def test_qyro_version(mock_version):
    mock_version.return_value = "1.2.3"
    metadata = PackageMetadata()
    assert metadata.qyro_version() == "1.2.3"
