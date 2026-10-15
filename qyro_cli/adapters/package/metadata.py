"""
Concrete adapter for retrieving Qyro package metadata.
"""

from importlib.metadata import PackageNotFoundError, version


class PackageMetadata:
    def cli_version(self) -> str:
        try:
            return version("qyro-cli")
        except PackageNotFoundError:
            pass

        try:
            import qyro_cli
            if hasattr(qyro_cli, "__version__"):
                return str(qyro_cli.__version__)
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

    def qyro_version(self) -> str:
        return self.cli_version()