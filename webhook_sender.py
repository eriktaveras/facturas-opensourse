import requests
import json
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from models import WebhookEndpoint

logger = logging.getLogger(__name__)

class WebhookSender:
    def __init__(self):
        self.timeout = 5 # segundos

    def trigger_event(self, db: Session, event_name: str, data: dict, org_id: int = None):
        """
        Dispara un evento a todos los suscriptores activos.
        """
        try:
            # Buscar suscriptores
            query = db.query(WebhookEndpoint).filter(WebhookEndpoint.is_active == True)
            if org_id:
                query = query.filter(WebhookEndpoint.organization_id == org_id)
            webhooks = query.all()
            
            subscribers = []
            for wh in webhooks:
                try:
                    events = json.loads(wh.events or "[]")
                    if event_name in events or "*" in events:
                        subscribers.append(wh)
                except:
                    continue
            
            if not subscribers:
                return {"status": "no_subscribers"}

            # Preparar payload estándar
            payload_dict = {
                "event": event_name,
                "timestamp": datetime.utcnow().isoformat(),
                "data": data
            }
            payload_str = json.dumps(payload_dict)
            
            results = []
            
            # Enviar a cada uno (idealmente esto iría a una cola de tareas async)
            for wh in subscribers:
                try:
                    headers = {
                        "Content-Type": "application/json",
                        "User-Agent": "InvoiceFlow-Webhook/1.0",
                        "X-InvoiceFlow-Event": event_name
                    }
                    
                    logger.info(f"📡 Enviando webhook {event_name} a {wh.url}")
                    
                    response = requests.post(
                        wh.url,
                        data=payload_str,
                        headers=headers,
                        timeout=self.timeout
                    )
                    
                    results.append({
                        "id": wh.id,
                        "url": wh.url,
                        "status": response.status_code,
                        "success": 200 <= response.status_code < 300
                    })
                    
                except Exception as e:
                    logger.error(f"❌ Error enviando webhook a {wh.url}: {e}")
                    results.append({
                        "id": wh.id,
                        "url": wh.url,
                        "error": str(e),
                        "success": False
                    })
            
            return {"status": "completed", "results": results}
            
        except Exception as e:
            logger.error(f"Error general en trigger_event: {e}")
            return {"status": "error", "message": str(e)}
