# Velto CLI - Kivy Icon PyInstaller Runtime Hook
# Executed by PyInstaller at application boot prior to App.run()
import sys
import shutil
from pathlib import Path
from kivy.config import Config

def _apply_qyro_kivy_icon() -> None:
    """
    Forces Kivy to load user's custom icon instead of Kivy's default logo (kivy-icon-64.png).
    """
    try:
        # Determine PyInstaller extracted base directory (_MEIPASS or executable parent)
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.argv[0]).resolve().parent))

        # Build candidate list prioritised by OS platform and resolution
        plat_folder = "mac" if sys.platform == "darwin" else ("windows" if sys.platform == "win32" else "linux")

        icon_candidates = [
            # Platform specific (mac/linux/windows)
            base_dir / "resources" / plat_folder / "icons" / "1024.png",
            base_dir / "resources" / plat_folder / "icons" / "512.png",
            base_dir / "resources" / plat_folder / "icons" / "256.png",
            base_dir / "resources" / plat_folder / "icons" / "128.png",
            base_dir / "resources" / plat_folder / "icons" / "64.png",
            base_dir / "resources" / plat_folder / "icons" / "32.png",
            base_dir / "resources" / plat_folder / "icons" / "icon.png",
            base_dir / "resources" / plat_folder / "icon.png",
            # Base fallback
            base_dir / "resources" / "base" / "icons" / "256.png",
            base_dir / "resources" / "base" / "icons" / "128.png",
            base_dir / "resources" / "base" / "icons" / "64.png",
            base_dir / "resources" / "base" / "icons" / "32.png",
            base_dir / "resources" / "base" / "icons" / "24.png",
            base_dir / "resources" / "base" / "icons" / "16.png",
            base_dir / "resources" / "base" / "icons" / "icon.png",
            base_dir / "resources" / "base" / "icon.png",
            # Flat resources folder
            base_dir / "resources" / "icons" / "512.png",
            base_dir / "resources" / "icons" / "256.png",
            base_dir / "resources" / "icons" / "128.png",
            base_dir / "resources" / "icons" / "64.png",
            base_dir / "resources" / "icons" / "32.png",
            base_dir / "resources" / "icons" / "icon.png",
            base_dir / "resources" / "icon.png",
            base_dir / "icon.png",
        ]

        user_icon = next((cand for cand in icon_candidates if cand.is_file()), None)
        if not user_icon:
            return

        try:
            Config.set("kivy", "window_icon", str(user_icon))
        except Exception:
            pass

        logo_dirs = [
            base_dir / "kivy" / "data" / "logo",
            base_dir / "kivy_install" / "data" / "logo",
        ]

        # Scan for matching resolution files or overwrite all default kivy-icon-* files
        for logo_dir in logo_dirs:
            if logo_dir.is_dir():
                for logo_file in logo_dir.glob("kivy-icon-*"):
                    try:
                        shutil.copy2(user_icon, logo_file)
                    except Exception:
                        pass
    except Exception:
        pass


_apply_qyro_kivy_icon()
