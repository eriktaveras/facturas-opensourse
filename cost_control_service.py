import time
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import Invoice

@dataclass
class OpenAICostInfo:
    """Información de costos de una llamada a OpenAI"""
    tokens_used: int
    cost_usd: float
    model: str
    processing_time: float

class CostControlService:
    """
    Servicio para controlar y monitorear costos de OpenAI
    """
    
    # Precios por 1000 tokens (actualizar según precios actuales de OpenAI)
    MODEL_COSTS = {
        "gpt-4o": {
            "input": 0.005,   # $0.005 per 1K input tokens
            "output": 0.015   # $0.015 per 1K output tokens
        },
        "gpt-4": {
            "input": 0.03,
            "output": 0.06
        },
        "gpt-4-vision-preview": {
            "input": 0.01,
            "output": 0.03
        }
    }
    
    def __init__(self):
        self.daily_limit_usd = float(os.getenv("OPENAI_DAILY_LIMIT_USD", "10.0"))
        self.hourly_limit_requests = int(os.getenv("OPENAI_HOURLY_LIMIT_REQUESTS", "100"))
        self.request_history = []  # Para rate limiting
        
    def check_rate_limits(self) -> Dict[str, Any]:
        """
        Verifica si se pueden hacer más requests según rate limits
        """
        now = datetime.now()
        
        # Limpiar requests viejos (más de 1 hora)
        self.request_history = [
            req_time for req_time in self.request_history 
            if now - req_time < timedelta(hours=1)
        ]
        
        # Verificar límite horario
        if len(self.request_history) >= self.hourly_limit_requests:
            return {
                "allowed": False,
                "reason": "hourly_limit_exceeded",
                "requests_this_hour": len(self.request_history),
                "limit": self.hourly_limit_requests
            }
        
        return {
            "allowed": True,
            "requests_this_hour": len(self.request_history),
            "limit": self.hourly_limit_requests
        }
    
    def check_daily_cost_limit(self, db: Session, org_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Verifica si se ha excedido el límite diario de costos
        """
        today = datetime.now().date()
        today_start = datetime.combine(today, datetime.min.time())
        today_end = datetime.combine(today, datetime.max.time())
        
        # Calcular gasto del día
        filters = [
            Invoice.created_at >= today_start,
            Invoice.created_at <= today_end,
            Invoice.openai_cost_usd.isnot(None)
        ]
        if org_id:
            filters.append(Invoice.organization_id == org_id)

        daily_cost = db.query(func.sum(Invoice.openai_cost_usd)).filter(
            *filters
        ).scalar() or 0.0
        
        if daily_cost >= self.daily_limit_usd:
            return {
                "allowed": False,
                "reason": "daily_cost_limit_exceeded",
                "daily_cost": daily_cost,
                "limit": self.daily_limit_usd
            }
        
        return {
            "allowed": True,
            "daily_cost": daily_cost,
            "limit": self.daily_limit_usd,
            "remaining": self.daily_limit_usd - daily_cost
        }
    
    def can_process_request(self, db: Session, org_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Verifica si se puede procesar una nueva request
        """
        rate_check = self.check_rate_limits()
        if not rate_check["allowed"]:
            return rate_check
        
        cost_check = self.check_daily_cost_limit(db, org_id=org_id)
        if not cost_check["allowed"]:
            return cost_check
        
        return {
            "allowed": True,
            "rate_info": rate_check,
            "cost_info": cost_check
        }
    
    def record_request_start(self):
        """
        Registra el inicio de una request
        """
        self.request_history.append(datetime.now())
        return time.time()  # timestamp para medir duración
    
    def calculate_cost(
        self, 
        model: str, 
        input_tokens: int, 
        output_tokens: int
    ) -> float:
        """
        Calcula el costo de una llamada a OpenAI
        """
        if model not in self.MODEL_COSTS:
            print(f"⚠️ Modelo desconocido: {model}, usando precios de gpt-4o")
            model = "gpt-4o"
        
        costs = self.MODEL_COSTS[model]
        
        input_cost = (input_tokens / 1000) * costs["input"]
        output_cost = (output_tokens / 1000) * costs["output"]
        
        total_cost = input_cost + output_cost
        
        print(f"💰 Costo calculado: {model} | Input: {input_tokens} tokens (${input_cost:.4f}) | Output: {output_tokens} tokens (${output_cost:.4f}) | Total: ${total_cost:.4f}")
        
        return total_cost
    
    def record_openai_usage(
        self,
        invoice: Invoice,
        model: str,
        input_tokens: int,
        output_tokens: int,
        start_time: float,
        db: Session
    ) -> OpenAICostInfo:
        """
        Registra el uso de OpenAI y actualiza la factura
        """
        total_tokens = input_tokens + output_tokens
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        processing_time = time.time() - start_time
        
        # Actualizar factura
        invoice.openai_tokens_used = total_tokens
        invoice.openai_cost_usd = cost
        invoice.openai_model_used = model
        invoice.openai_processing_time = processing_time
        
        db.commit()
        
        cost_info = OpenAICostInfo(
            tokens_used=total_tokens,
            cost_usd=cost,
            model=model,
            processing_time=processing_time
        )
        
        print(f"📊 Uso registrado: {total_tokens} tokens, ${cost:.4f}, {processing_time:.2f}s")
        
        return cost_info
    
    def get_cost_statistics(self, db: Session, org_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Obtiene estadísticas de costos
        """
        base_filter = []
        if org_id:
            base_filter.append(Invoice.organization_id == org_id)

        # Estadísticas generales
        total_cost = db.query(func.sum(Invoice.openai_cost_usd)).filter(
            Invoice.openai_cost_usd.isnot(None),
            *base_filter
        ).scalar() or 0.0
        
        total_tokens = db.query(func.sum(Invoice.openai_tokens_used)).filter(
            Invoice.openai_tokens_used.isnot(None),
            *base_filter
        ).scalar() or 0
        
        total_requests = db.query(Invoice).filter(
            Invoice.processed == True,
            Invoice.openai_cost_usd.isnot(None),
            *base_filter
        ).count()
        
        # Estadísticas del día
        today = datetime.now().date()
        today_start = datetime.combine(today, datetime.min.time())
        today_end = datetime.combine(today, datetime.max.time())
        
        daily_cost = db.query(func.sum(Invoice.openai_cost_usd)).filter(
            Invoice.created_at >= today_start,
            Invoice.created_at <= today_end,
            Invoice.openai_cost_usd.isnot(None),
            *base_filter
        ).scalar() or 0.0
        
        daily_requests = db.query(Invoice).filter(
            Invoice.created_at >= today_start,
            Invoice.created_at <= today_end,
            Invoice.processed == True,
            Invoice.openai_cost_usd.isnot(None),
            *base_filter
        ).count()
        
        # Estadísticas por modelo
        model_stats = db.query(
            Invoice.openai_model_used,
            func.count(Invoice.id).label('count'),
            func.sum(Invoice.openai_cost_usd).label('total_cost'),
            func.sum(Invoice.openai_tokens_used).label('total_tokens')
        ).filter(
            Invoice.openai_model_used.isnot(None),
            *base_filter
        ).group_by(
            Invoice.openai_model_used
        ).all()
        
        # Convertir a diccionario
        model_breakdown = []
        for model, count, cost, tokens in model_stats:
            model_breakdown.append({
                "model": model,
                "requests": count,
                "total_cost": float(cost or 0),
                "total_tokens": int(tokens or 0),
                "avg_cost_per_request": float(cost or 0) / count if count > 0 else 0
            })
        
        # Estadísticas de los últimos 7 días
        week_ago = datetime.now() - timedelta(days=7)
        weekly_stats = db.query(
            func.date(Invoice.created_at).label('date'),
            func.sum(Invoice.openai_cost_usd).label('daily_cost'),
            func.count(Invoice.id).label('daily_requests')
        ).filter(
            Invoice.created_at >= week_ago,
            Invoice.openai_cost_usd.isnot(None),
            *base_filter
        ).group_by(
            func.date(Invoice.created_at)
        ).order_by(
            func.date(Invoice.created_at)
        ).all()
        
        weekly_breakdown = []
        for date, cost, requests in weekly_stats:
            date_str = date
            if hasattr(date, 'isoformat'):
                date_str = date.isoformat()
            
            weekly_breakdown.append({
                "date": date_str,
                "cost": float(cost or 0),
                "requests": requests
            })
        
        return {
            "total_cost": float(total_cost),
            "total_tokens": total_tokens,
            "total_requests": total_requests,
            "average_cost_per_request": float(total_cost) / total_requests if total_requests > 0 else 0,
            "daily": {
                "cost": float(daily_cost),
                "requests": daily_requests,
                "limit": self.daily_limit_usd,
                "remaining": self.daily_limit_usd - daily_cost
            },
            "rate_limits": {
                "hourly_limit": self.hourly_limit_requests,
                "current_hour_requests": len(self.request_history)
            },
            "model_breakdown": model_breakdown,
            "weekly_breakdown": weekly_breakdown
        }
    
    def get_cost_alerts(self, db: Session, org_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Verifica si hay alertas de costos
        """
        stats = self.get_cost_statistics(db, org_id=org_id)
        alerts = []
        
        # Alerta de límite diario
        if stats["daily"]["cost"] >= self.daily_limit_usd * 0.8:  # 80% del límite
            alerts.append({
                "type": "daily_limit_warning",
                "severity": "warning" if stats["daily"]["cost"] < self.daily_limit_usd else "critical",
                "message": f"Límite diario al {(stats['daily']['cost'] / self.daily_limit_usd) * 100:.1f}%",
                "current": stats["daily"]["cost"],
                "limit": self.daily_limit_usd
            })
        
        # Alerta de rate limiting
        if len(self.request_history) >= self.hourly_limit_requests * 0.9:  # 90% del límite
            alerts.append({
                "type": "rate_limit_warning",
                "severity": "warning",
                "message": f"Rate limit al {(len(self.request_history) / self.hourly_limit_requests) * 100:.1f}%",
                "current": len(self.request_history),
                "limit": self.hourly_limit_requests
            })
        
        return {
            "has_alerts": len(alerts) > 0,
            "alerts": alerts
        } 
