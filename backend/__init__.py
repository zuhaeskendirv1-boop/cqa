from pathlib import Path
from dotenv import load_dotenv

# load repo-root .env before any module reads os.environ
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
