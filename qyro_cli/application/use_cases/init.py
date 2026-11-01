"""
qyro.application.use_cases.init_project
Orchestrates project initialization following Clean Architecture.
"""

from pathlib import Path

from qyro_cli.application.ports import (
    FileSystemPort,
    SettingsPort,
    TemplateProviderPort,
    UserInteractionPort,
    DependencyInstallerPort,
)
from qyro_cli.domain.errors import ProjectAlreadyExistsError
from qyro_cli.domain.project import (
    AddonModule,
    Binding,
    ProjectConfig,
    TargetPlatform,
)
from qyro_cli.domain.version import Version


class InitProjectUseCase:
    def __init__(
        self,
        ui: UserInteractionPort,
        fs: FileSystemPort,
        dependencies: DependencyInstallerPort,
        templates: TemplateProviderPort,
        settings_repo: SettingsPort,
    ):
        self.ui = ui
        self.fs = fs
        self.dependencies = dependencies
        self.templates = templates
        self.settings = settings_repo

    def execute(
        self,
        target_dir: str = ".",
        default_binding: str | None = None,
        template_version: str | None = None,
        target_platform: str | None = None,
        app_name: str | None = None,
        app_version: str | None = None,
        author: str | None = None,
        addons: list[str] | None = None,
        bundle_id: str | None = None,
        confirm: bool | None = None,
    ) -> None:
        destination = Path(target_dir).resolve()
        src_path = destination / "main.py"

        if self.fs.exists(str(src_path)):
            raise ProjectAlreadyExistsError(str(destination))

        interactive = all(
            value is None
            for value in (
                target_platform,
                app_name,
                app_version,
                author,
                addons,
                bundle_id,
                confirm,
            )
        )

        if interactive:
            self.ui.welcome()

        platform_options = {
            "iPhone": TargetPlatform.IPHONE,
            "Android": TargetPlatform.ANDROID,
            "Desktop (x86_64)": TargetPlatform.X86_64,
            "Desktop (Apple Silicon)": TargetPlatform.APPLE_SILICON,
        }

        if target_platform:
            normalized_platform = target_platform.strip().lower().replace("_", "-")
            platform_aliases = {
                "desktop": TargetPlatform.X86_64,
                "x86": TargetPlatform.X86_64,
                "x86-64": TargetPlatform.X86_64,
                "x86_64": TargetPlatform.X86_64,
                "apple-silicon": TargetPlatform.APPLE_SILICON,
                "apple silicon": TargetPlatform.APPLE_SILICON,
                "iphone": TargetPlatform.IPHONE,
                "android": TargetPlatform.ANDROID,
            }
            selected_platform = platform_aliases.get(normalized_platform)
            if selected_platform is None:
                raise ValueError(
                    "Invalid target platform. Supported values: "
                    "desktop, x86_64, apple-silicon, iphone, android."
                )
        else:
            platform_str = self.ui.ask_choice(
                "Select target platform",
                list(platform_options),
                default="Desktop (x86_64)",
            )
            selected_platform = platform_options[platform_str]

        available_bindings = [
            binding
            for binding in Binding
            if binding.supports(selected_platform)
        ]

        if default_binding:
            binding = Binding.parse(default_binding)

            if binding not in available_bindings:
                raise ValueError(
                    f"Binding '{binding.value}' is not available "
                    f"for {selected_platform.value}."
                )
        else:
            binding_str = self.ui.ask_choice(
                "Select UI binding",
                [binding.value for binding in available_bindings],
                default="PySide6",
            )
            binding = Binding.parse(binding_str)

        default_app = destination.name if target_dir != "." else "MyApp"
        app_name_value = app_name or self.ui.ask_text(
            "App name",
            default=default_app,
        )

        version_str = app_version or self.ui.ask_text(
            "Version",
            default="1.0.0",
        )
        version = Version.parse(version_str)

        author_value = author or self.ui.ask_text(
            "Author",
            default=self.fs.current_user(),
        )

        addon_options = {
            "Hot Reloading (⚠️ Experimental)": AddonModule.HOTRL,
            "Pydux (Redux-style State Management)": AddonModule.PYDUX,
            "Sentry (Crash Reporting Integration)": AddonModule.SENTRY,
            "Requests (HTTP Library)": AddonModule.REQUESTS,
        }

        if selected_platform in (
            TargetPlatform.IPHONE,
            TargetPlatform.ANDROID,
        ):
            addon_options = {
                "Pydux (Redux-style State Management)": AddonModule.PYDUX,
            }

        if addons is None:
            if interactive:
                selected_addons_raw = self.ui.ask_multi_choice(
                    "Select optional add-ons to configure:",
                    list(addon_options),
                )

                selected_addons = [
                    addon_options[option]
                    for option in selected_addons_raw
                    if option in addon_options
                ]
            else:
                selected_addons = []
        else:
            allowed = {addon.value: addon for addon in addon_options.values()}
            selected_addons = [
                allowed[value]
                for value in addons
                if value in allowed
            ]

        bundle_identifier = bundle_id

        if selected_platform in (
            TargetPlatform.IPHONE,
            TargetPlatform.APPLE_SILICON,
        ):
            bundle_default = (
                f"com.{author_value.lower().split()[0]}."
                f"{''.join(app_name_value.lower().split())}"
            )

            label = (
                "iOS bundle identifier"
                if selected_platform == TargetPlatform.IPHONE
                else f"Mac bundle identifier (e.g. {bundle_default}, optional)"
            )

            if bundle_identifier is None:
                bundle_identifier = self.ui.ask_text(
                    label,
                    default=bundle_default,
                    show_default=selected_platform == TargetPlatform.IPHONE,
                )

            if (
                selected_platform == TargetPlatform.APPLE_SILICON
                and not bundle_identifier.strip()
            ):
                bundle_identifier = None

        config = ProjectConfig(
            app_name=app_name_value,
            version=version,
            author=author_value,
            binding=binding,
            target_platform=selected_platform,
            mac_bundle_identifier=bundle_identifier or None,
            addons=selected_addons,
        )

        self.ui.show_summary(
            "Please Confirm",
            config.as_display_dict(),
        )

        should_continue = (
            confirm
            if confirm is not None
            else self.ui.confirm("Create project with these settings?")
        )
        if not should_continue:
            self.ui.info("Operation aborted by user.")
            return

        self.ui.info("Resolving boilerplate template...")
        template_dir = self.templates.resolve_template(
            binding=config.binding,
            target_platform=config.target_platform,
            version=(
                Version.parse(template_version)
                if template_version
                else None
            ),
        )

        self.fs.copy_tree(
            template_dir,
            destination,
        )

        self.fs.render_tree(
            str(destination),
            {
                **config.as_template_variables(),
                **config.as_base_settings(),
            },
        )

        self.ui.info("Installing project dependencies...")
        self.dependencies.install(str(destination))

        self.ui.success(
            f"\n✓ Project initialized successfully in "
            f"[bold #ff8c00]{target_dir}/[/bold #ff8c00]"
        )
        self.ui.info("\nNext steps:")

        if target_dir != ".":
            self.ui.info(
                f"  [bold #ff8c00]cd {target_dir}[/bold #ff8c00]"
            )

        self.ui.info(
            "  [bold #ff8c00]qyro start[/bold #ff8c00]"
        )
