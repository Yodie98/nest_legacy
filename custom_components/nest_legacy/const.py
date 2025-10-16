"""Constants for the Nest integration."""

from __future__ import annotations

import logging
from typing import Final

LOGGER: logging.Logger = logging.getLogger(__package__)

DOMAIN: Final = "nest_legacy"
ATTRIBUTION: Final = "Data provided by Google/Nest"

CONF_ACCOUNT_TYPE: Final = "account_type"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_ISSUE_TOKEN: Final = "issue_token"
CONF_COOKIES: Final = "cookies"
CONF_FIELD_TEST: Final = "field_test"
