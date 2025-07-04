import json
import os
import requests
import logging
import pandas as pd
import re
from datetime import datetime
from flask import Flask, jsonify, render_template, request, g, Response

# --- Custom Imports ---
from logger_setup import initialize_session_dir, configure_app_logger
from parser import parse_workbook

# --- Configuration & Constants ---
CONFIG_FILE = 'config.json'
PROMPTS_DIR = './prompts'
NEBIUS_API_BASE_URL = "https://api.studio.nebius.com/v1"

# --- App State ---
session_wallets = []

def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "api_key": "", "starred_models": [], "all_models": [], "selected_model": ""
        }
        save_config(default_config)
        return default_config
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def save_config(config_data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config_data, f, indent=2)

# --- Flask App Initialization ---
app = Flask(__name__, static_folder='static', template_folder='templates')
config = load_config()

SESSION_DIR_PATH = initialize_session_dir()
configure_app_logger(app, SESSION_DIR_PATH)

@app.before_request
def before_request_func():
    g.session_dir = SESSION_DIR_PATH

# --- API Endpoints ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    global config
    if request.method == 'POST':
        config = request.json
        save_config(config)
        app.logger.info("Configuration updated and saved.")
        return jsonify({"status": "success"})
    return jsonify(config)

@app.route('/api/prompts', methods=['GET'])
def get_prompts():
    if not os.path.exists(PROMPTS_DIR): os.makedirs(PROMPTS_DIR)
    return jsonify([f for f in os.listdir(PROMPTS_DIR) if f.endswith('.txt')])

@app.route('/api/validate-key', methods=['POST'])
def validate_key():
    api_key = request.json.get('api_key')
    app.logger.info("Attempting to validate API key.")
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get(f"{NEBIUS_API_BASE_URL}/models", headers=headers, timeout=10)
        response.raise_for_status()
        app.logger.info("API key validation successful.")
        return jsonify({"valid": True})
    except requests.RequestException as e:
        app.logger.error(f"API key validation failed: {e}", exc_info=True)
        return jsonify({"valid": False, "error": str(e)}), 500

@app.route('/api/models', methods=['GET'])
def fetch_models():
    api_key = config.get('api_key')
    app.logger.info("Fetching model list from API.")
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get(f"{NEBIUS_API_BASE_URL}/models", headers=headers, timeout=10)
        response.raise_for_status()
        model_ids = [model['id'] for model in response.json().get('data', [])]
        app.logger.info(f"Successfully fetched {len(model_ids)} models.")
        return jsonify(model_ids)
    except requests.RequestException as e:
        app.logger.error(f"Failed to fetch models: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/parse-files', methods=['POST'])
def parse_files():
    global session_wallets
    session_wallets.clear()
    files = request.files.getlist('files')
    if not files: return jsonify({"error": "No files uploaded"}), 400
    filenames = [f.filename for f in files]
    app.logger.info(f"Received {len(filenames)} files for parsing: {', '.join(filenames)}")
    temp_dir = os.path.join(g.session_dir, 'temp_uploads')
    os.makedirs(temp_dir, exist_ok=True)
    for file in files:
        filepath = os.path.join(temp_dir, file.filename)
        file.save(filepath)
        try:
            profiles_dict = parse_workbook(filepath)
            session_wallets.extend(list(profiles_dict.values()))
        except Exception as e:
            app.logger.error(f"Failed to parse file '{file.filename}': {e}", exc_info=True)
            return jsonify({"error": f"Failed to parse {file.filename}."}), 500
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifact_path = os.path.join(g.session_dir, f"parsed_{ts}.json")
    with open(artifact_path, 'w') as f:
        json.dump(session_wallets, f, indent=2)
    app.logger.info(f"Parsing complete. {len(session_wallets)} wallets processed. Saved to {artifact_path}")
    return jsonify({"wallets": session_wallets})

@app.route('/api/learn-filter-stream', methods=['POST'])
def learn_filter_stream():
    data = request.json
    selected_wallets, model, prompt_template_file = data.get('wallets'), data.get('model'), data.get('promptTemplate')
    api_key = config.get('api_key')
    app.logger.info(f"--- STREAMING LEARN FILTER WORKFLOW STARTED ---")
    app.logger.info(f"Model: '{model}', Prompt: '{prompt_template_file}'.")
    try:
        with open(os.path.join(PROMPTS_DIR, prompt_template_file), 'r') as f:
            prompt_template = f.read()
    except Exception as e:
        app.logger.error(f"CRITICAL: Could not read prompt file: {e}", exc_info=True)
        return Response(f"Error reading prompt file: {e}", status=500)
    wallets_json_string = json.dumps(selected_wallets, indent=2)
    final_prompt = prompt_template.replace('${wallets_json}', wallets_json_string)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": final_prompt}], "stream": True}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    req_path = os.path.join(g.session_dir, f"ai_request_{ts}.json")
    with open(req_path, 'w') as f: json.dump(payload, f, indent=2)
    app.logger.info(f"AI request payload saved. Starting stream...")
    def generate():
        full_response_content = ""
        try:
            response = requests.post(f"{NEBIUS_API_BASE_URL}/chat/completions", headers=headers, json=payload, stream=True, timeout=(10, 60))
            response.raise_for_status()
            app.logger.info("Stream opened successfully. Receiving data...")
            for chunk in response.iter_lines():
                if chunk:
                    chunk_str = chunk.decode('utf-8')
                    if chunk_str.startswith('data: '):
                        json_str = chunk_str[6:]
                        if json_str.strip() == '[DONE]':
                            break
                        try:
                            json_data = json.loads(json_str)
                            delta = json_data.get('choices', [{}])[0].get('delta', {})
                            content_chunk = delta.get('content', '')
                            if content_chunk:
                                full_response_content += content_chunk
                                yield f"data: {json.dumps({'token': content_chunk})}\n\n"
                        except json.JSONDecodeError:
                            app.logger.warning(f"Could not decode JSON from stream chunk: {json_str}")
                            continue
            app.logger.info("Stream finished.")
            final_completion_artifact = {"id": "streamed_response", "object": "chat.completion", "created": int(datetime.now().timestamp()), "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": full_response_content}, "finish_reason": "stop"}]}
            resp_path = os.path.join(g.session_dir, f"ai_completion_{ts}.json")
            with open(resp_path, 'w') as f: json.dump(final_completion_artifact, f, indent=2)
            app.logger.info(f"Full completion artifact saved to {resp_path}")
        except requests.exceptions.Timeout:
            app.logger.error("CRITICAL: AI API call timed out.")
            yield f"data: {json.dumps({'error': 'Request timed out.'})}\n\n"
        except Exception as e:
            app.logger.error(f"CRITICAL: Error during stream: {e}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/execute', methods=['POST'])
def execute_code():
    code_to_run = request.json.get('code')
    app.logger.info(f"--- EXECUTE CODE WORKFLOW STARTED ---")
    app.logger.info(f"Code snippet to execute (first 100 chars): {code_to_run[:100].strip()}...")
    if not session_wallets:
        app.logger.error("Execution attempted but no wallets are loaded.")
        return jsonify({"error": "No wallets loaded to filter."}), 400
    app.logger.info(f"Executing against {len(session_wallets)} loaded wallets.")
    try:
        df = pd.DataFrame(session_wallets)
        app.logger.info(f"Created pandas DataFrame for execution. Shape: {df.shape}")
        local_namespace = {'pd': pd, 'df': df}
        app.logger.info(">>> EXECUTING DYNAMIC CODE VIA EXEC()...")
        exec(code_to_run, {}, local_namespace)
        app.logger.info(">>> DYNAMIC CODE EXECUTION COMPLETE.")
        app.logger.info("Attempting to retrieve 'result_df' from execution namespace...")
        result_df = local_namespace.get('result_df')
        if result_df is None:
            app.logger.error("CRITICAL: 'result_df' was not found in the namespace.")
            raise ValueError("'result_df' was not defined in the executed code.")
        app.logger.info(f"SUCCESS: Retrieved 'result_df'. Type: {type(result_df)}, Shape: {result_df.shape}")
        results = result_df.to_dict(orient='records')
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_path = os.path.join(g.session_dir, f"results_{ts}.json")
        with open(results_path, 'w') as f: json.dump(results, f, indent=2)
        app.logger.info(f"Execution complete. Filtered {len(results)} wallets. Saved to {results_path}")
        return jsonify({"results": results})
    except Exception as e:
        app.logger.error(f"CRITICAL: Code execution failed: {e}", exc_info=True)
        return jsonify({"error": f"Code execution failed: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)