from core.config import (
    APP_NAME, VERSION, BASE_DIR, CONFIG_FILE,
    load_config, save_config, config_transaction, SkipSave,
    default_config, migrate_config_if_needed, validate_target, validate_port, log,
)
from core.routes import app, verify_ui_file
