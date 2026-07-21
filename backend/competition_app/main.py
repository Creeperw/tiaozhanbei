from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


app = create_app(ApplicationContainer.build(Settings.from_env()))


if __name__ == "__main__":
    from competition_app.cli.app import app as cli_app

    cli_app()