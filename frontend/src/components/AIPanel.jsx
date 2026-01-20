import React, { useState } from 'react';
import axios from 'axios';
import { BrainCircuit, Loader2 } from 'lucide-react';

const AIPanel = ({ analysis, levels, account }) => {
  const [loading, setLoading] = useState(false);
  const [aiResponse, setAiResponse] = useState('');

  const handleAnalyze = async () => {
    if (!levels.entry || !levels.sl || !levels.tp) {
      alert("Сначала установите уровни!");
      return;
    }
    setLoading(true);
    try {
      const res = await axios.post('http://127.0.0.1:5000/api/analysis/analyze', {
        ...levels,
        balance: account.balance,
        equity: account.equity,
        ai_context: analysis?.ai_transcript // передаем данные, которые прислал бэкенд
      });
      setAiResponse(res.data.analysis || res.data.error);
    } catch (err) {
        console.error("AI Analysis Error:", err); // Теперь err используется!
        setAiResponse("Ошибка связи с ИИ. Проверь, запущен ли Python сервер.");
      } finally {
      setLoading(false);
    }
  };

  return (
    <aside className="glass-panel sidebar-right">
      <h3>AI Gemini Analysis</h3>
      
      <div className="ai-content-area">
        {aiResponse ? (
          <div className="ai-text-box">{aiResponse}</div>
        ) : (
          <div className="ai-placeholder">Ожидание торгового плана...</div>
        )}
      </div>

      <div className="panel-footer">
        <button 
          className="ai-btn" 
          onClick={handleAnalyze}
          disabled={loading}
        >
          {loading ? <Loader2 className="spinner" /> : <BrainCircuit size={18} />}
          {loading ? "Анализирую..." : "Запустить AI Анализ"}
        </button>
      </div>
    </aside>
  );
};

export default AIPanel;