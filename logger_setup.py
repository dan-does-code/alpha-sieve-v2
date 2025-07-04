import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

LOGS_DIR = './LOGS'
SESSION_ENV_VAR = 'ALPHA_SIEVE_SESSION_PATH'

def initialize_session_dir():
    """
    Checks for a session path in an environment variable. If not found,
    creates a unique session directory and sets the environment variable.
    """
    session_path = os.environ.get(SESSION_ENV_VAR)
    if session_path and os.path.exists(session_path):
        return session_path

    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR)

    existing_sessions = [d for d in os.listdir(LOGS_DIR) if d.startswith('session_')]
    next_session_num = len(existing_sessions) + 1
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir_name = f"session_{next_session_num}_{timestamp}"
    session_dir_path = os.path.join(LOGS_DIR, session_dir_name)
    os.makedirs(session_dir_path)

    os.environ[SESSION_ENV_VAR] = session_dir_path
    return session_dir_path

def configure_app_logger(app, session_path):
    """
    Configures the entire logging system to unify app logs and request logs
    into a single, consistently formatted stream to both file and console.
    """
    # 1. Define a standard format for ALL log messages
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )

    # 2. Create the handler for writing to the session log file
    log_file = os.path.join(session_path, 'main.log')
    file_handler = RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=5)
    file_handler.setFormatter(formatter)
    
    # 3. Create the handler for writing to the console (the terminal)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 4. Hijack the Werkzeug logger (handles the GET/POST requests)
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.handlers.clear()
    werkzeug_logger.addHandler(file_handler)
    werkzeug_logger.addHandler(console_handler)
    werkzeug_logger.setLevel(logging.INFO)
    werkzeug_logger.propagate = False

    # 5. Configure the main app's logger
    app.logger.handlers.clear()
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False

    app.logger.info(f"Unified logger configured for session: {os.path.basename(session_path)}")