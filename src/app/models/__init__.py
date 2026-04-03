# Re-export everything from the original flat models module so existing
# imports (e.g. ``from app.models import ChartOfAccounts``) keep working.
from app.models._core import *  # noqa: F401,F403
from app.models.budget import *  # noqa: F401,F403
from app.models.journal import *  # noqa: F401,F403
