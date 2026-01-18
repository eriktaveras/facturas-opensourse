import json
import asyncio
from typing import List, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime

from models import SessionLocal, Notification, Invoice

class WebSocketManager:
    """
    Gestor de conexiones WebSocket para notificaciones en tiempo real
    """
    
    def __init__(self):
        # Lista de conexiones activas con org asociada
        self.active_connections: List[Dict[str, Any]] = []
        # Estadísticas de notificaciones
        self.notification_count = 0
        
    async def connect(self, websocket: WebSocket, org_id: int):
        """Conectar un nuevo cliente WebSocket"""
        await websocket.accept()
        self.active_connections.append({"socket": websocket, "org_id": org_id})
        print(f"📡 Nueva conexión WebSocket. Total: {len(self.active_connections)}")
        
        # Enviar mensaje de bienvenida
        await self.send_personal_message({
            "type": "connection_established",
            "message": "Conectado al sistema de notificaciones",
            "timestamp": datetime.now().isoformat()
        }, websocket)
    
    def disconnect(self, websocket: WebSocket):
        """Desconectar cliente WebSocket"""
        self.active_connections = [
            conn for conn in self.active_connections if conn["socket"] is not websocket
        ]
        print(f"📡 Conexión WebSocket cerrada. Total: {len(self.active_connections)}")
    
    async def send_personal_message(self, message: Dict[str, Any], websocket: WebSocket):
        """Enviar mensaje a un cliente específico"""
        try:
            await websocket.send_text(json.dumps(message))
        except Exception as e:
            print(f"❌ Error enviando mensaje personal: {e}")
            self.disconnect(websocket)
    
    async def broadcast(self, message: Dict[str, Any], org_id: int = None):
        """Enviar mensaje a todos los clientes conectados y guardar en BD"""
        
        # 1. Guardar en Base de Datos (Persistencia)
        if message.get("type") not in ["heartbeat", "connection_established", "statistics_update"]:
            try:
                db = SessionLocal()
                resolved_org_id = org_id
                try:
                    invoice_id = message.get("data", {}).get("invoice_id")
                    if invoice_id:
                        inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
                        resolved_org_id = inv.organization_id if inv else None
                except Exception:
                    resolved_org_id = org_id

                notification = Notification(
                    type=message.get("severity", "info") if message.get("type") == "cost_alert" else "info", # Simplificado
                    title=message.get("type", "Notificación"),
                    message=message.get("message", ""),
                    data=json.dumps(message.get("data", {})),
                    read=False,
                    organization_id=resolved_org_id
                )
                
                # Mapeo específico de tipos
                if message.get("type") == "processing_complete":
                    notification.type = "success" if message.get("data", {}).get("success") else "error"
                    notification.title = "Procesamiento Completado"
                elif message.get("type") == "whatsapp_image_received":
                    notification.type = "info"
                    notification.title = "Nuevo Mensaje WhatsApp"
                elif message.get("type") == "invoice_uploaded":
                    notification.type = "success"
                    notification.title = "Archivo Subido"
                elif message.get("type") == "cost_alert":
                    notification.type = message.get("severity", "warning")
                    notification.title = "Alerta de Costos"
                
                db.add(notification)
                db.commit()
                db.close()
            except Exception as e:
                print(f"❌ Error guardando notificación en BD: {e}")

        # 2. Enviar por WebSocket (Tiempo Real)
        if not self.active_connections:
            # print("📡 No hay conexiones WebSocket activas")
            return
        
        message["timestamp"] = datetime.now().isoformat()
        message_str = json.dumps(message)
        
        print(f"📡 Broadcasting a {len(self.active_connections)} clientes: {message.get('type', 'unknown')}")
        
        # Enviar a todas las conexiones activas
        disconnected = []
        for connection in self.active_connections:
            try:
                if org_id is None or connection["org_id"] == org_id:
                    await connection["socket"].send_text(message_str)
            except Exception as e:
                print(f"❌ Error enviando a conexión: {e}")
                disconnected.append(connection["socket"])
        
        # Remover conexiones fallidas
        for connection in disconnected:
            self.disconnect(connection)
    
    async def notify_new_whatsapp_image(self, sender_info: Dict[str, Any], invoice_id: int, org_id: int = None):
        """
        Notificar sobre nueva imagen recibida por WhatsApp
        """
        await self.broadcast({
            "type": "whatsapp_image_received",
            "message": f"📱 Nueva factura recibida de {sender_info.get('name', 'Usuario')} - ID: {invoice_id}",
            "data": {
                "invoice_id": invoice_id,
                "sender": {
                    "name": sender_info.get('name', 'Usuario WhatsApp'),
                    "phone": sender_info.get('phone', 'Desconocido')
                },
                "status": "processing"
            }
        }, org_id=org_id)
        self.notification_count += 1
    
    async def notify_processing_complete(self, invoice_id: int, result: Dict[str, Any], org_id: int = None):
        """
        Notificar cuando se complete el procesamiento con datos detallados
        """
        success = result.get("success", False)
        
        if success and result.get("data"):
            extracted_data = result["data"]
            vendor = extracted_data.get("vendor_name", "N/A")
            amount = extracted_data.get("total_amount", 0)
            currency = extracted_data.get("currency", "USD")
            transaction_type = extracted_data.get("transaction_type", "unknown")
            category = extracted_data.get("category", "Sin categoría")
            
            # Formatear cantidad
            try:
                formatted_amount = f"{currency} {amount:,.2f}" if amount else "N/A"
            except:
                formatted_amount = f"{currency} {amount}" if amount else "N/A"
            
            # Emoji según tipo de transacción
            type_emoji = "💰" if transaction_type == "income" else "💸" if transaction_type == "expense" else "📄"
            
            message = f"✅ Factura procesada - {vendor} | {formatted_amount} | {category} {type_emoji}"
        else:
            error_msg = result.get("error", "Error desconocido")
            message = f"❌ Error procesando factura ID {invoice_id}: {error_msg}"
        
        await self.broadcast({
            "type": "processing_complete",
            "message": message,
            "data": {
                "invoice_id": invoice_id,
                "success": success,
                "result": result,
                "extracted_data": result.get("data") if success else None
            }
        }, org_id=org_id)
    
    async def notify_cost_alert(self, alert_info: Dict[str, Any], org_id: int = None):
        """
        Notificar alertas de costos
        """
        await self.broadcast({
            "type": "cost_alert",
            "message": alert_info.get("message", "Alerta de costos"),
            "data": alert_info,
            "severity": alert_info.get("severity", "info")
        }, org_id=org_id)
    
    async def notify_new_invoice_upload(self, invoice_id: int, filename: str, org_id: int = None):
        """
        Notificar sobre nueva factura subida manualmente
        """
        await self.broadcast({
            "type": "invoice_uploaded",
            "message": f"Nueva factura subida: {filename}",
            "data": {
                "invoice_id": invoice_id,
                "filename": filename
            }
        }, org_id=org_id)
    
    async def notify_statistics_update(self, stats: Dict[str, Any], org_id: int = None):
        """
        Notificar actualización de estadísticas
        """
        await self.broadcast({
            "type": "statistics_update",
            "message": "Estadísticas actualizadas",
            "data": stats
        }, org_id=org_id)
    
    async def send_heartbeat(self):
        """
        Enviar heartbeat para mantener conexiones vivas
        """
        if self.active_connections:
            await self.broadcast({
                "type": "heartbeat",
                "connections": len(self.active_connections),
                "notifications_sent": self.notification_count
            })
    
    def get_status(self) -> Dict[str, Any]:
        """
        Obtener estado del WebSocket manager
        """
        return {
            "active_connections": len(self.active_connections),
            "notifications_sent": self.notification_count,
            "status": "active" if self.active_connections else "idle"
        }

# Instancia global del manager
websocket_manager = WebSocketManager()

async def start_heartbeat_task():
    """
    Tarea en background para enviar heartbeats periódicos
    """
    while True:
        try:
            await websocket_manager.send_heartbeat()
            await asyncio.sleep(30)  # Heartbeat cada 30 segundos
        except Exception as e:
            print(f"❌ Error en heartbeat: {e}")
            await asyncio.sleep(5) 
