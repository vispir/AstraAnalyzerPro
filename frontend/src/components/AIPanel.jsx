import React, { useState } from 'react';
import axios from 'axios';
import { BrainCircuit, Loader2, AlertTriangle, CheckCircle2, Clock } from 'lucide-react';

const AIPanel = ({ analysis, levels, account }) => {
  const [loading, setLoading] = useState(false);
  const [aiData, setAiData] = useState(null); // Для хранения распарсенного JSON
  const [rawText, setRawText] = useState(''); // Для текста (запасной вариант)

  const handleAnalyze = async () => {
    if (!levels.entry || !levels.sl || !levels.tp) {
      alert("Установите все уровни на графике!");
      return;
    }
    setLoading(true);
    setAiData(null);
    setRawText('');

    try {
      const res = await axios.post('http://127.0.0.1:5000/api/analysis/analyze', {
        entry: levels.entry,
        sl: levels.sl,
        tp: levels.tp,
        balance: account.balance,
        equity: account.equity,
        ai_context: analysis?.ai_transcript || {}
      });

      const responseContent = res.data.analysis || "";

      // Пытаемся распарсить JSON
      try {
        // Очищаем текст от возможных Markdown кавычек ```json ... ```
        const jsonStr = responseContent.replace(/```json|```/g, '').trim();
        const parsed = JSON.parse(jsonStr);
        setAiData(parsed);
      } catch {
        // Если это не JSON, выводим как обычный текст, очистив от звездочек
        setRawText(responseContent.replace(/\*/g, ''));
      }

    } catch (err) {
      console.error("AI Error:", err);
      setRawText("Ошибка ИИ. Проверь консоль сервера или VPN.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <aside className="glass-panel sidebar-right" style={{display: 'flex', flexDirection: 'column'}}>
      <h3>AI Gemini Analysis</h3>
      
      <div className="ai-content-scroll" style={{flex: 1, overflow: 'auto'}}>
        {loading ? (
          <div className="ai-loading">
            <Loader2 className="spinner" size={32} />
            <p>Парсинг Price Action...</p>
          </div>
        ) : (
          <>
            {/* ВАРИАНТ 1: КРАСИВЫЙ JSON (Если парсинг удался) */}
            {aiData && (
              <div className="structured-analysis">
                <div className={`verdict-badge ${aiData.decision?.toLowerCase()}`}>
                   {aiData.decision === 'ВХОДИТЬ' && <CheckCircle2 size={16} />}
                   {aiData.decision === 'ЖДАТЬ' && <Clock size={16} />}
                   {aiData.decision === 'ОТМЕНА' && <AlertTriangle size={16} />}
                   {aiData.decision}
                </div>
                
                <div className="analysis-section">
                  <label>Логика структуры</label>
                  <p>{aiData.logic}</p>
                </div>

                <div className="analysis-section">
                  <label>Корректировка плана</label>
                  <p className="correction-text">{aiData.correction}</p>
                </div>
                
                {aiData.risk_score && (
                  <div className="risk-meter">
                    <label>Оценка риска: {aiData.risk_score}/10</label>
                    <div className="meter-bg">
                      <div className="meter-fill" style={{width: `${aiData.risk_score * 10}%`}}></div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ВАРИАНТ 2: ОБЫЧНЫЙ ТЕКСТ (Если это не JSON) */}
            {rawText && <div className="ai-text-fallback">{rawText}</div>}

            {!aiData && !rawText && (
              <div className="ai-placeholder">Настройте уровни и запустите аудит</div>
            )}
          </>
        )}
      </div>

      <button className="ai-btn" onClick={handleAnalyze} disabled={loading} style={{marginTop: 'auto'}}>
        {!loading && <BrainCircuit size={18} />}
        {loading ? "Обработка..." : "Запустить AI Анализ"}
      </button>
    </aside>
  );
};

export default AIPanel;
