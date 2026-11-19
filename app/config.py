
from pathlib import Path
from fastapi.templating import Jinja2Templates

# Location of the app folder
BASE_DIR = Path(__file__).resolve().parent

# Connect the HTML templates folder
templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)
