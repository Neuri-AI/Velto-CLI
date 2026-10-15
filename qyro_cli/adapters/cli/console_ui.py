"""
Concrete presentation adapter implementing UserInteractionPort
using Rich and Questionary.
"""

import random
from pathlib import Path
from typing import Sequence

import questionary
from prompt_toolkit.styles import Style
from rich.theme import Theme
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

QUESTIONARY_STYLE = Style([
    ("qmark", "fg:#c08457 bold"),
    ("question", "fg:#f1f5f9 bold"),
    ("answer", "fg:#fdba74 bold"),
    ("pointer", "fg:#fb923c bold"),
    ("highlighted", "fg:#fb923c bg:default bold"), 
    ("selected", "fg:#fdba74 bg:default"),
    ("instruction", "fg:#9ca3af"),
])


custom_theme = Theme(
    {
        "prompt.choices": "bold white",
        "prompt.default": "bold #ff8c00",
    }
)
class RichConsoleUI:
    def __init__(self, console: Console | None = None):
        self._console = console or Console(
            force_terminal=True,
            color_system="truecolor",
            theme=custom_theme,
            highlight=False,
        )

    def _ascii_fallback(self, message: str) -> str:
        safe = (
            message
            .replace("⏳", "...")
            .replace("💔", "Error")
            .replace("🚀", "*")
            .replace("✓", "OK")
            .replace("⚠️", "WARN")
            .replace("⚠", "WARN")
        )
        return safe.encode("ascii", errors="replace").decode("ascii")

    def _print(self, renderable: object) -> None:
        try:
            self._console.print(renderable)
        except UnicodeEncodeError:
            if isinstance(renderable, str):
                self._console.print(self._ascii_fallback(renderable))
                return
            raise

    def welcome(self) -> None:
        messages = [
            "What are we building today?",
            "What will you build today?",
            "Let's build something great.",
            "Your next project starts here.",
        ]

        message = random.choice(messages)
        directory = Path.cwd().name

        logo = Text(
            "\n"
            " ██████╗ ██╗   ██╗██████╗  ██████╗\n"
            "██╔═══██╗╚██╗ ██╔╝██╔══██╗██╔═══██╗\n"
            "██║   ██║ ╚████╔╝ ██████╔╝██║   ██║\n"
            "██║▄▄ ██║  ╚██╔╝  ██╔══██╗██║   ██║\n"
            "╚██████╔╝   ██║   ██║  ██║╚██████╔╝\n"
            " ╚══▀▀═╝    ╚═╝   ╚═╝  ╚═╝ ╚═════╝\n",
            style="bold #ff8c00",
        )

        title = Text(message, style="bold white")
        subtitle = Text(
            f"Creating your next application in {directory}/",
            style="dim",
        )

        content = Group(
            Align.center(logo),
            Align.center(title),
            Align.center(subtitle),
        )

        self._console.print(
            Panel(
                content,
                border_style="#ff8c00",
                padding=(1, 4),
            )
        )

    def ask_text(
        self,
        prompt: str,
        default: str = "",
        show_default: bool = True,
    ) -> str:
        return Prompt.ask(
            f"[bold]{prompt}[/bold]",
            default=default,
            show_default=show_default,
            console=self._console
        )

    def ask_choice(
        self,
        prompt: str,
        choices: Sequence[str],
        default: str = "",
    ) -> str:
        options = list(choices)

        if not options:
            return default

        answer = questionary.select(
            prompt,
            choices=options,
            default=default or options[0],
            style=QUESTIONARY_STYLE,
        ).ask()

        if answer is None:
            raise KeyboardInterrupt

        return answer

    def ask_multi_choice(
        self,
        prompt: str,
        choices: Sequence[str],
    ) -> list[str]:
        answer = questionary.checkbox(
            prompt,
            choices=[
                questionary.Choice(
                    choice,
                    value=choice,
                    checked=False,
                )
                for choice in choices
            ],
            style=QUESTIONARY_STYLE,
        ).ask()

        if answer is None:
            raise KeyboardInterrupt

        return answer

    def confirm(
        self,
        prompt: str,
        default: bool = True,
    ) -> bool:
        return Confirm.ask(
            f"[bold #fff]{prompt}[/bold #fff]",
            default=default,
            console=self._console,
        )

    def show_summary(
        self,
        title: str,
        fields: dict[str, str],
    ) -> None:
        table = Table(
            title="Project Configuration",
            show_header=False,
            box=None,
        )

        for label, value in fields.items():
            table.add_row(
                f"{label}:",
                f"[#fdba74]{value}[/#fdba74]",
            )

        self._console.print(
            Panel(
                table,
                title=f"[bold]{title}[/bold]",
                border_style="#ff8c00",
            )
        )

    def info(self, message: str) -> None:
        self._print(message)

    def success(self, message: str) -> None:
        self._print(
            f"[bold #fff]{message}[/bold #fff]"
        )

    def warning(self, message: str) -> None:
        self._print(
            f"[#fdba74]{message}[/#fdba74]"
        )

    def progress(self, message: str) -> None:
        self._print(
            f"[#fdba74]⏳ {message}[/#fdba74]"
        )

    def error(self, message: str) -> None:
        self._print(
            f"\n💔 [bold red]Error:[/bold red] {message}"
        )