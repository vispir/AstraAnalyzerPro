import React, { useState } from 'react';
import axios from 'axios';
import { BrainCircuit, Loader2, AlertTriangle, CheckCircle2, Clock } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:5000/api";

const AIPanel = ({ levels, account, analysis }) => {
  const [loading, setLoading] = useState(false);
  const [aiData, setAiData] = useState(null);
  const [rawText, setRawText] = useState('');
  const [selectedModel, setSelectedModel] = useState('gemini');
  const [selectedLanguage, setSelectedLanguage] = useState('ru');
  
  const [userIdea, setUserIdea] = useState('');

  const t = {
    ru: {
      model: "Модель LLM",
      language: "Язык",
      userIdea: "Ваша торговая идея (опционально)",
      placeholderIdea: "Напишите свои мысли по рынку...",
      analyze: "Запустить AI Анализ",
      processing: "Обработка...",
      placeholder: "Нажмите кнопку для запуска AI анализа рынка",
      parsing: "Парсинг Price Action...",
      summary: "Резюме",
      tradePlan: "Торговый план",
      rrAnalysis: "R:R Анализ",
      invalidation: "Инвалидация",
      waitReason: "Причина ожидания",
      triggerCondition: "Условие входа",
      waitTime: "Ожидаемое время"
    },
    en: {
      model: "LLM Model",
      language: "Language",
      userIdea: "Your Trading Idea (optional)",
      placeholderIdea: "Write your market thoughts...",
      analyze: "Run AI Analysis",
      processing: "Processing...",
      placeholder: "Press the button to start AI market analysis",
      parsing: "Parsing Price Action...",
      summary: "Executive Summary",
      tradePlan: "Trade Plan",
      rrAnalysis: "R:R Analysis",
      invalidation: "Invalidation",
      waitReason: "Wait Reason",
      triggerCondition: "Trigger Condition",
      waitTime: "Estimated Wait Time"
    }
  }[selectedLanguage];

  const handleAnalyze = async () => {
    setLoading(true);
    setAiData(null);
    setRawText('');

    try {
      const res = await axios.post(`${API_BASE}/llm/analyze`, {
        entry: levels.entry || null,
        sl: levels.sl || null,
        tp: levels.tp || null,
        balance: account.balance,
        daily_loss_limit: account.dailyLossLimit,
        risk_percent: account.riskPercent,
        model: selectedModel,
        language: selectedLanguage,
        user_idea: userIdea
      });

      // Проверяем формат ответа от LLM
      const responseContent = res.data.response || res.data.analysis || "";
      const parsedDecision = res.data.parsed_decision;

      if (parsedDecision) {
        // Если уже есть распарсенный JSON от сервера
        setAiData(parsedDecision);
      } else {
        // Пытаемся распарсить самостоятельно
        try {
          const jsonStr = responseContent.replace(/```json|```/g, '').trim();
          const parsed = JSON.parse(jsonStr);
          setAiData(parsed);
        } catch {
          setRawText(responseContent.replace(/\*/g, ''));
        }
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
      <h3>AI Market Analysis</h3>
      {/* Быстрый SMC Радар (Обновляется каждые 30 сек бесплатно) */}
    {analysis && (
      <div style={{
        background: 'rgba(255, 255, 255, 0.03)',
        borderRadius: '8px',
        padding: '10px',
        marginBottom: '15px',
        border: '1px solid rgba(255, 255, 255, 0.05)'
      }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
      <span style={{ fontSize: '11px', color: '#888' }}>TREND</span>
      <span style={{ 
        fontSize: '11px', 
        fontWeight: 'bold', 
        color: analysis.trend === 'UPTREND' ? '#4ade80' : '#f87171' 
      }}>
        {analysis.trend}
      </span>
    </div>
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
      <span style={{ fontSize: '11px', color: '#888' }}>ZONE</span>
      <span style={{ 
        fontSize: '11px', 
        fontWeight: 'bold', 
        color: analysis.advanced?.key_levels?.Current_Zone === 'PREMIUM' ? '#fbbf24' : '#60a5fa' 
      }}>
        {analysis.advanced?.key_levels?.Current_Zone}
      </span>
    </div>
    {analysis.bos && analysis.bos.length > 0 && (
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '11px', color: '#888' }}>LAST BOS</span>
        <span style={{ fontSize: '11px', color: '#fff' }}>
          {analysis.bos[analysis.bos.length - 1].price}
        </span>
      </div>
    )}
  </div>
)}

      
      
      <div style={{display: 'flex', gap: '8px', marginBottom: '8px'}}>
        <div style={{flex: 1}}>
          <label style={{display: 'block', fontSize: '11px', marginBottom: '4px', color: '#b0b0b0'}}>
            {t.model}
          </label>
          <select 
            value={selectedModel} 
            onChange={(e) => setSelectedModel(e.target.value)}
            disabled={loading}
            style={{
              width: '100%',
              padding: '8px 12px',
              borderRadius: '8px',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              background: '#0f172a',
              color: '#fff',
              fontSize: '12px',
              cursor: 'pointer',
              outline: 'none',
              appearance: 'auto',
              boxShadow: '0 2px 4px rgba(0,0,0,0.2)'
            }}
          >
            <option value="gemini" style={{ background: '#1e293b' }}>Gemini API - Gemini 3 Preview</option>
            <option value="gateway" style={{ background: '#1e293b' }}>AI Gateway - Gemini 3 Pro</option>
            <option value="openrouter" style={{ background: '#1e293b' }}>OpenRouter - DeepSeek R1</option>
          </select>
        </div>
        
        <div style={{width: '70px'}}>
          <label style={{display: 'block', fontSize: '11px', marginBottom: '4px', color: '#b0b0b0'}}>
            {t.language}
          </label>
          <select 
            value={selectedLanguage} 
            onChange={(e) => setSelectedLanguage(e.target.value)}
            disabled={loading}
            style={{
              width: '100%',
              padding: '8px 8px',
              borderRadius: '8px',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              background: '#0f172a',
              color: '#fff',
              fontSize: '12px',
              cursor: 'pointer',
              outline: 'none',
              appearance: 'auto',
              boxShadow: '0 2px 4px rgba(0,0,0,0.2)'
            }}
          >
            <option value="ru">RU</option>
            <option value="en">EN</option>
          </select>
        </div>
      </div>

      <div style={{marginBottom: '10px'}}>
        <label style={{display: 'block', fontSize: '11px', marginBottom: '4px', color: '#b0b0b0'}}>
          {t.userIdea}
        </label>
        <textarea
          value={userIdea}
          onChange={(e) => setUserIdea(e.target.value)}
          placeholder={t.placeholderIdea}
          disabled={loading}
          style={{
            width: '100%',
            height: '80px',
            padding: '8px 12px',
            borderRadius: '8px',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            background: '#0f172a',
            color: '#fff',
            fontSize: '12px',
            resize: 'none',
            outline: 'none',
            marginBottom: '8px'
          }}
        />
      </div>
      
      <div className="ai-content-scroll" style={{flex: 1, overflow: 'auto'}}>
        {loading ? (
          <div className="ai-loading">
            <Loader2 className="spinner" size={32} />
            <p>{t.parsing}</p>
          </div>
        ) : (
          <>
            {aiData && (
              <div className="structured-analysis">
                {/* Сигнал действия */}
                {aiData.signal && (
                  <div className={`verdict-badge ${aiData.signal.action?.toLowerCase()}`}>
                    {aiData.signal.action === 'BUY' && <CheckCircle2 size={16} />}
                    {aiData.signal.action === 'SELL' && <CheckCircle2 size={16} />}
                    {aiData.signal.action === 'WAIT' && <Clock size={16} />}
                    <span>{aiData.signal.action}</span>
                    {aiData.signal.confidence && (
                      <span style={{marginLeft: '8px', fontSize: '12px', opacity: 0.8}}>
                        ({aiData.signal.confidence}%)
                      </span>
                    )}
                  </div>
                )}
                
                {/* Резюме */}
                {aiData.executive_summary && (
                  <div className="analysis-section">
                    <label>{t.summary}</label>
                    <p>{aiData.executive_summary}</p>
                  </div>
                )}

                {/* Торговый план (для BUY/SELL) */}
                {aiData.trade_plan && (
                  <>
                    <div className="analysis-section">
                      <label>{t.tradePlan}</label>
                      <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginTop: '8px'}}>
                        <div style={{fontSize: '12px'}}>
                          <strong>Entry:</strong> {aiData.trade_plan.final_entry}
                        </div>
                        <div style={{fontSize: '12px'}}>
                          <strong>SL:</strong> {aiData.trade_plan.final_sl}
                        </div>
                        <div style={{fontSize: '12px'}}>
                          <strong>TP:</strong> {aiData.trade_plan.final_tp}
                        </div>
                        {aiData.signal?.setup_type && (
                          <div style={{fontSize: '12px'}}>
                            <strong>Setup:</strong> {aiData.signal.setup_type}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Math Debug (Risk/Reward) */}
                    {aiData.math_debug_log && (
                      <div className="analysis-section">
                        <label>{t.rrAnalysis}</label>
                        <div style={{fontSize: '12px', marginTop: '8px'}}>
                          <div>Risk: ${aiData.math_debug_log.risk_amount?.toFixed(2)}</div>
                          <div>Reward: ${aiData.math_debug_log.reward_amount?.toFixed(2)}</div>
                          <div style={{color: '#4ade80', fontWeight: 'bold'}}>
                            Ratio: 1:{aiData.math_debug_log.calculated_rr?.toFixed(2)}
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Условие инвалидации */}
                    {aiData.trade_plan.invalidation_condition && (
                      <div className="analysis-section">
                        <label>{t.invalidation}</label>
                        <p style={{fontSize: '12px', color: '#fbbf24'}}>{aiData.trade_plan.invalidation_condition}</p>
                      </div>
                    )}
                  </>
                )}

                {/* Wait Metadata (для WAIT) */}
                {aiData.wait_metadata && (
                  <>
                    <div className="analysis-section">
                      <label>{t.waitReason}</label>
                      <p style={{fontSize: '12px', color: '#fbbf24'}}>
                        {aiData.wait_metadata.wait_reason_code}
                      </p>
                    </div>
                    
                    {aiData.wait_metadata.trigger_condition && (
                      <div className="analysis-section">
                        <label>{t.triggerCondition}</label>
                        <p style={{fontSize: '12px'}}>{aiData.wait_metadata.trigger_condition}</p>
                      </div>
                    )}
                    
                    {aiData.wait_metadata.estimated_wait_time && (
                      <div className="analysis-section">
                        <label>{t.waitTime}</label>
                        <p style={{fontSize: '12px'}}>{aiData.wait_metadata.estimated_wait_time}</p>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {rawText && (
              <div className="ai-text-fallback" style={{
                fontSize: '13px',
                lineHeight: '1.6',
                whiteSpace: 'pre-wrap',
                wordWrap: 'break-word'
              }}>
                {rawText}
              </div>
            )}

            {!aiData && !rawText && (
              <div className="ai-placeholder">{t.placeholder}</div>
            )}
          </>
        )}
      </div>

      <button className="ai-btn" onClick={handleAnalyze} disabled={loading} style={{marginTop: 'auto'}}>
        {!loading && <BrainCircuit size={18} />}
        {loading ? t.processing : t.analyze}
      </button>
    </aside>
  );
};

export default AIPanel;
