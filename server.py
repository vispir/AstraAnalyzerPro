"""
Astra Analyzer Pro - Minimal Web Service for Render
Serves cron endpoint for session_breakout_trader
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)


@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


@app.route('/api/cron/session_breakout', methods=['GET'])
def trigger_session_breakout():
    """
    Cron endpoint for Session Breakout Trader v4.0.
    Called every 15 minutes by external cron (e.g. cron-job.org).
    """
    auth_header = request.headers.get('Authorization')
    cron_secret = os.getenv('CRON_SECRET')

    if cron_secret and auth_header != f'Bearer {cron_secret}':
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from session_breakout_trader import check_session_breakout
        result = check_session_breakout()
        return jsonify({"success": True, "result": result})
    except Exception as e:
        logger.error(f"Session breakout error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
