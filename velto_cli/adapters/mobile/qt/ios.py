"""PySide6 iOS deployment."""
from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest


class QtIOSBuilder:
    def _pyside_ios(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        root = request.project_root
        spec = root / "pysidedeploy.spec"
        command = self._command("pyside6-ios-deploy")
        if not spec.is_file():
            raise RuntimeError(
                "Provide pysidedeploy.spec with matching iOS PySide6 and "
                "shiboken6 wheels before running the iOS deploy tool."
            )
        args = [command, "--config-file", str(spec)]
        if request.init_only:
            args.append("--init")
        self._run(args, root)
        if request.init_only:
            return None
        projects = sorted(root.glob("deployment/**/*.xcodeproj"))
        if not projects:
            raise RuntimeError("iOS deployment did not produce an Xcode project")
        return MobileBuildArtifact(projects[-1], request.target, request.framework,
                                   "xcode-project")
