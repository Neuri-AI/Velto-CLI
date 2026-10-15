"""
Concrete adapter for retrieving Qyro package metadata.
"""

from importlib.metadata import PackageNotFoundError, version


class PackageMetadata:
    def cli_version(self) -> str:
        try:
            return version("velto-cli")
        except PackageNotFoundError:
            pass

        try:
            import velto_cli
            if hasattr(velto_cli, "__version__"):
                return str(velto_cli.__version__)
        except Exception:
            pass

        return "unknown"

    def engine_version(self) -> str:
        for pkg_name in ("qyro-engine", "qyro"):
            try:
                return version(pkg_name)
            except PackageNotFoundError:
                continue

        try:
            import qyro
            if hasattr(qyro, "__version__"):
                return str(qyro.__version__)
        except Exception:
            pass

        return "unknown"

    def velto_version(self) -> str:
        return self.cli_version()
