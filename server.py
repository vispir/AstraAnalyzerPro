import MetaTrader5 as mt5
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

TF_MAP = {
    "M15": mt5.TIMEFRAME_M15,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4
}

def get_mt5_data(target_tf):
    if not mt5.initialize():
        return {"error": "MT5 не запущен"}

    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True) 
    acc_info = mt5.account_info()
    
    # Конфиг свечей для покрытия нужных периодов
    TF_CONFIG = {
        "M15": {"tf": mt5.TIMEFRAME_M15, "count": 200},
        "H1":  {"tf": mt5.TIMEFRAME_H1, "count": 200},
        "H4":  {"tf": mt5.TIMEFRAME_H4, "count": 180}
    }
    config = TF_CONFIG.get(target_tf, TF_CONFIG["M15"])
    
    rates = mt5.copy_rates_from_pos(symbol, config["tf"], 0, config["count"])
    if rates is None or len(rates) == 0:
        return {"error": f"Нет данных для {target_tf}"}

    candles = []
    for r in rates:
        candles.append({
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4])
        })

    # Контекст для ИИ (сокращенный OHLC)
    def get_ai_data(tf_code, count):
        r = mt5.copy_rates_from_pos(symbol, tf_code, 0, count)
        if r is None: return []
        return [{"h": float(x[2]), "l": float(x[3]), "c": float(x[4])} for x in r]

    return {
        "balance": acc_info.balance if acc_info else 0,
        "equity": acc_info.equity if acc_info else 0,
        "history": candles,
        "ai_context": {
            "M15": get_ai_data(mt5.TIMEFRAME_M15, 30),
            "H1":  get_ai_data(mt5.TIMEFRAME_H1, 20),
            "H4":  get_ai_data(mt5.TIMEFRAME_H4, 15)
        }
    }

@app.route('/stats')
def stats():
    selected_tf = request.args.get('tf', 'M15')
    return jsonify(get_mt5_data(selected_tf))

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        login = data.get('login')
        password = data.get('password')
        server = data.get('server')

        if not login or not password or not server:
            return jsonify({"success": False, "error": "Не все поля заполнены"}), 400

        if not mt5.initialize():
            return jsonify({"success": False, "error": "MT5 не запущен"}), 500

        result = mt5.login(int(login), password, server)

        if result:
            return jsonify({"success": True, "message": "Подключение успешно"})
        else:
            error_code = mt5.last_error()
            return jsonify({"success": False, "error": f"Ошибка подключения: код {error_code[0]}, {error_code[1]}"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": f"Ошибка сервера: {str(e)}"}), 500

if __name__ == '__main__':
    print("--- ASTRA SERVER: PRO MULTI-TF ACTIVE ---")
    app.run(port=5000)