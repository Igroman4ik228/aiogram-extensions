"""Inspect the installed package without loading aiogram or contacting Telegram."""

from importlib.metadata import metadata
from importlib.resources import files

import aiogram_extensions


def main() -> None:
    package = metadata("aiogram-extensions")
    marker = files(aiogram_extensions).joinpath("py.typed")

    print(f"Package: {package['Name']}")
    print(f"License: {package['License-Expression']}")
    print(f"Typing marker: {'present' if marker.is_file() else 'missing'}")


if __name__ == "__main__":
    main()
