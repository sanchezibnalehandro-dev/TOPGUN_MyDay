from __future__ import annotations

import argparse
from topgun_myday.resources import resource_path
from topgun_myday.ui import MyDayApp


def main() -> None:
    parser = argparse.ArgumentParser(description="TOPGUN · Мой день")
    parser.add_argument(
        "--smoke-ui",
        action="store_true",
        help="Создать интерфейс, обработать демо-файл и закрыться.",
    )
    args = parser.parse_args()

    app = MyDayApp()
    if args.smoke_ui:
        app.withdraw()
        app.load_path(resource_path("data", "demo_topgun.xlsx"))
        app.update_idletasks()
        app.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
