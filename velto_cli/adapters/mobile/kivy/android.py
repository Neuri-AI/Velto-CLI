"""Kivy Android Buildozer deployment."""
import re
from velto_cli.domain.mobile import MobileBuildArtifact, MobileFreezeRequest


class KivyAndroidBuilder:
    def _kivy_android(self, request: MobileFreezeRequest) -> MobileBuildArtifact | None:
        root = request.project_root
        spec = root / "buildozer.spec"
        if request.init_only:
            if spec.exists():
                raise FileExistsError(f"Refusing to overwrite {spec}")
            name = re.sub(r"[^a-z0-9]", "", request.app_name.lower()) or "veltoapp"
            spec.write_text(
                "[app]\n"
                f"title = {request.app_name}\n"
                f"package.name = {name}\n"
                "package.domain = org.velto\n"
                f"version = {request.version}\n"
                "source.dir = .\n"
                "source.include_exts = py,png,jpg,kv,atlas,json,css,svg\n"
                "requirements = python3,kivy,qyro-engine,velto-core\n"
                "orientation = all\n"
                "android.archs = arm64-v8a\n"
                "\n[buildozer]\nlog_level = 2\n",
                encoding="utf-8",
            )
            return None
        if not spec.is_file():
            raise RuntimeError(
                "Missing buildozer.spec. Run 'velto build --target android "
                "--init-spec', then review requirements and package.domain."
            )
        if request.entry_point != "main.py":
            raise ValueError("Buildozer expects main.py at the project root")
        command = self._command("buildozer")
        self._run([command, "android", "debug" if request.debug else "release"], root)
        artifact = self._latest(root / "bin", (".apk",) if request.debug else (".aab", ".apk"))
        return MobileBuildArtifact(artifact, request.target, request.framework,
                                   artifact.suffix.lstrip("."))
